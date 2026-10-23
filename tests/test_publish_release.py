"""Publishing checks never upload or require package-index credentials."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/publish_release.py"


@unittest.skipUnless(SCRIPT.is_file(), "Publishing helper is maintained outside the frozen release")
class PublishingChecks(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("publish_release_checks", SCRIPT)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.manifest = "".join(digest + "  " + name + "\n" for name, digest in self.helper.FILES.items()).encode()

    def test_approved_manifest(self):
        self.assertEqual(self.helper.parse_manifest(self.manifest), self.helper.FILES)

    def test_index_metadata_retries_temporary_not_found(self):
        url = "https://test.pypi.org/pypi/matrixswarm-hornet/" + self.helper.VERSION + "/json"
        missing = HTTPError(url, 404, "Not Found", None, None)
        with patch.object(self.helper, "fetch", side_effect=[missing, b'{"ready": true}']) as fetch:
            with patch.object(self.helper.time, "sleep") as sleep:
                self.assertEqual(self.helper.fetch_index_metadata(url), {"ready": True})
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(self.helper.INDEX_DISCOVERY_DELAY_SECONDS)

    def test_index_metadata_retry_limit_is_enforced(self):
        url = "https://test.pypi.org/pypi/matrixswarm-hornet/" + self.helper.VERSION + "/json"
        missing = HTTPError(url, 404, "Not Found", None, None)
        with patch.object(self.helper, "INDEX_DISCOVERY_ATTEMPTS", 2):
            with patch.object(self.helper, "fetch", side_effect=missing) as fetch:
                with patch.object(self.helper.time, "sleep") as sleep:
                    with self.assertRaises(HTTPError):
                        self.helper.fetch_index_metadata(url)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(self.helper.INDEX_DISCOVERY_DELAY_SECONDS)

    def test_index_metadata_other_http_errors_are_not_retried(self):
        url = "https://test.pypi.org/pypi/matrixswarm-hornet/" + self.helper.VERSION + "/json"
        forbidden = HTTPError(url, 403, "Forbidden", None, None)
        with patch.object(self.helper, "fetch", side_effect=forbidden) as fetch:
            with patch.object(self.helper.time, "sleep") as sleep:
                with self.assertRaises(HTTPError):
                    self.helper.fetch_index_metadata(url)
        self.assertEqual(fetch.call_count, 1)
        sleep.assert_not_called()

    def test_index_metadata_invalid_json_is_not_retried(self):
        with patch.object(self.helper, "fetch", return_value=b"invalid") as fetch:
            with patch.object(self.helper.time, "sleep") as sleep:
                with self.assertRaises(json.JSONDecodeError):
                    self.helper.fetch_index_metadata("https://test.pypi.org/pypi/example/json")
        self.assertEqual(fetch.call_count, 1)
        sleep.assert_not_called()

    def test_duplicate_manifest_entry_refused(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            self.helper.parse_manifest(self.manifest + self.manifest.splitlines(keepends=True)[0])

    def test_changed_package_hash_refused(self):
        with self.assertRaisesRegex(RuntimeError, "approved"):
            self.helper.parse_manifest(b"0" * 64 + self.manifest[64:])

    def test_path_escape_in_manifest_refused(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid"):
            self.helper.parse_manifest(self.manifest + b"0" * 64 + b"  ../outside.whl\n")

    def test_changed_signed_manifest_stops_before_gpg_or_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.helper, "fetch", return_value=b"changed") as fetch:
                with patch.object(self.helper.subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "manifest changed"):
                        self.helper.prepare(Path(directory) / "fresh")
            self.assertEqual(fetch.call_count, 1)
            run.assert_not_called()

    def test_index_hash_mismatch_stops_before_installation(self):
        info = {"info": {"version": self.helper.VERSION}, "urls": [
            {"filename": name, "digests": {"sha256": "0" * 64}, "yanked": False}
            for name in self.helper.FILES]}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.helper, "fetch", return_value=json.dumps(info).encode()):
                with patch.object(self.helper.venv, "EnvBuilder") as builder:
                    with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                        self.helper.verify_index("testpypi", Path(directory) / "fresh")
            builder.assert_not_called()

    def test_unexpected_download_host_refused(self):
        info = {"info": {"version": self.helper.VERSION}, "urls": [
            {"filename": name, "digests": {"sha256": digest}, "yanked": False,
             "url": "https://untrusted.example/package.whl"}
            for name, digest in self.helper.FILES.items()]}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.helper, "fetch", return_value=json.dumps(info).encode()) as fetch:
                with self.assertRaisesRegex(RuntimeError, "download host"):
                    self.helper.verify_index("testpypi", Path(directory) / "fresh")
            self.assertEqual(fetch.call_count, 1)

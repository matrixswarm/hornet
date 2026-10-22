"""Verify the frozen public beta and exercise its installation from a package index.

This script never uploads. Uploading belongs to the separate OIDC publishing job.
Update these pins only when a new signed GitHub release is ready for publication.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import venv


VERSION = "0.15.1"
PROJECT = "matrixswarm-hornet"
SOURCE_COMMIT = "eb63c734b31bb1f5b095a408d641a68671ab53a0"
PRIMARY = "8086C2A9440B9226FA7A78E5BC0180CF9A935966"
SUBKEY = "42FEF65AA81AD6567DEA209175EB12C3FF293394"
MANIFEST_SHA256 = "1ede5b73d0b538fbbf0427d8b62908c4fcbcde5397628a07a511b11f347ba91e"
FILES = {
    "matrixswarm_hornet-0.15.1-py3-none-any.whl": "1411906898999f813697de16a6fabecef78fe246a2c80bc82de741aa2d8c2023",
    "matrixswarm_hornet-0.15.1.tar.gz": "bbb64f8dd947be59ca520904e0a11c3b1408dc832acdf98f7ecc02faca12fab8",
}
INDEXES = {"testpypi": "https://test.pypi.org", "pypi": "https://pypi.org"}
RELEASE = "https://github.com/matrixswarm/hornet/releases/download/v" + VERSION + "/"
ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Hornet-release-verification"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    require(len(data) <= 8 * 1024 * 1024, "Unexpected download size")
    return data


def parse_manifest(data):
    hashes = {}
    for line in data.decode("ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        require(match is not None, "Invalid checksum manifest entry")
        digest, name = match.groups()
        require(name not in hashes, "Duplicate checksum entry")
        hashes[name] = digest
    require(all(hashes.get(name) == digest for name, digest in FILES.items()),
            "Manifest does not match the approved package files")
    return hashes


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=False)
    dist = directory / "dist"
    dist.mkdir()
    manifest = fetch(RELEASE + "SHA256SUMS")
    require(sha(manifest) == MANIFEST_SHA256, "Release manifest changed")
    hashes = parse_manifest(manifest)
    (directory / "SHA256SUMS").write_bytes(manifest)
    (directory / "SHA256SUMS.asc").write_bytes(fetch(RELEASE + "SHA256SUMS.asc"))
    gpg = shutil.which("gpg")
    if not gpg and os.name == "nt":
        gpg = str(Path(os.environ["ProgramFiles"]) / "GnuPG/bin/gpg.exe")
    require(gpg is not None and Path(gpg).is_file(), "GnuPG is required")
    with tempfile.TemporaryDirectory(prefix="hornet-public-key-") as temporary:
        base = [gpg, "--no-options", "--homedir", temporary, "--batch", "--no-auto-key-retrieve"]
        subprocess.run(base + ["--import", str(ROOT / "signing/matrixswarm-release-signing-key.asc")],
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run(base + ["--status-fd", "1", "--verify",
                                str(directory / "SHA256SUMS.asc"), str(directory / "SHA256SUMS")],
                                capture_output=True, text=True, timeout=30)
    rows = [line.split() for line in result.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
    require(result.returncode == 0 and len(rows) == 1 and rows[0][2] == SUBKEY and rows[0][-1] == PRIMARY,
            "MatrixSwarm release signature did not verify")
    validation = fetch(RELEASE + "VALIDATION.json")
    require(sha(validation) == hashes.get("VALIDATION.json"), "Validation record changed")
    record = json.loads(validation)
    require(record["version"] == VERSION and record["source_commit"] == SOURCE_COMMIT,
            "Unexpected release identity")
    for name, digest in FILES.items():
        data = fetch(RELEASE + name)
        require(sha(data) == digest, "Package checksum mismatch: " + name)
        (dist / name).write_bytes(data)
    (directory / "verification.json").write_text(json.dumps({
        "version": VERSION, "source_commit": SOURCE_COMMIT, "manifest_sha256": MANIFEST_SHA256,
        "signature_verified": True, "files": FILES,
    }, indent=2) + "\n", encoding="utf-8")
    print("Verified the signed release and both unchanged package files.", flush=True)


def verify_index(index, directory):
    host = INDEXES[index]
    info = json.loads(fetch(host + "/pypi/" + PROJECT + "/" + VERSION + "/json"))
    require(info["info"]["version"] == VERSION, "Unexpected index version")
    indexed = {item["filename"]: item for item in info["urls"]}
    require(set(indexed) == set(FILES), "Index does not contain exactly the two approved distributions")
    for name, digest in FILES.items():
        item = indexed[name]
        require(not item.get("yanked") and item["digests"]["sha256"] == digest,
                "Index checksum mismatch or yanked file: " + name)
        url = urllib.parse.urlsplit(item["url"])
        require(url.scheme == "https" and url.hostname in {"files.pythonhosted.org", "test-files.pythonhosted.org"},
                "Unexpected index download host")
        require(sha(fetch(item["url"])) == digest, "Downloaded index file differs: " + name)
    directory.mkdir(parents=True, exist_ok=False)
    environment = directory / "environment"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirement = directory / "requirements.txt"
    wheel_hash = next(digest for name, digest in FILES.items() if name.endswith(".whl"))
    requirement.write_text(PROJECT + "==" + VERSION + " --hash=sha256:" + wheel_hash + "\n", encoding="ascii")
    subprocess.run([str(python), "-m", "pip", "--isolated", "install", "--no-cache-dir", "--no-deps",
                    "--only-binary=:all:", "--require-hashes", "--index-url", host + "/simple/",
                    "-r", str(requirement)], check=True, cwd=directory, timeout=120)
    subprocess.run([str(python), "-m", "pip", "check"], check=True, cwd=directory, timeout=30)
    result = subprocess.run([str(python), "-I", "-m", "hornet", "--version"], check=True,
                            capture_output=True, text=True, cwd=directory, timeout=30)
    require(result.stdout.strip() == PROJECT + " " + VERSION, "Installed CLI version differs")
    subprocess.run([str(python), str(ROOT / "starter/tools/first_run.py"),
                    "--project", str(directory / "first-goal")], check=True, cwd=directory, timeout=60)
    (directory / "index-verification.json").write_text(json.dumps({
        "index": index, "version": VERSION, "files": FILES,
        "downloads_verified": True, "fresh_install_passed": True, "first_goal_check_passed": True,
    }, indent=2) + "\n", encoding="utf-8")
    print("Verified downloads, fresh pip installation and first-goal checks on " + index + ".", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "verify-index"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--index", choices=sorted(INDEXES))
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args.directory.resolve())
    else:
        if not args.index:
            parser.error("verify-index requires --index")
        verify_index(args.index, args.directory.resolve())


if __name__ == "__main__":
    main()

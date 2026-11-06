"""Compatibility and isolated workspace checks for the shared interfaces."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hornet import coder
from hornet.core import Workspace, inventory, read_blueprint, read_state, require_ready
from hornet.core import ProxyResolver, RegistryManager, chat, discover_models


class CoreCoderTests(unittest.TestCase):
    def test_workspace_and_initialization_guard_share_existing_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            revision = workspace.create("example", kind="Python")
            root = workspace.revision("example", revision)
            self.assertEqual(workspace.revisions("example"), [revision])
            self.assertEqual(read_blueprint(root)["project"], "example")
            self.assertTrue(inventory(root))
            self.assertEqual(read_state(workspace, "example")["status"], "uninitialized")
            with self.assertRaisesRegex(ValueError, "Initialize"):
                require_ready(workspace, "example", revision)
            with self.assertRaisesRegex(ValueError, "Initialize"):
                workspace.fork("example", revision)

    def test_registry_service_respects_allowlist(self):
        resolver = ProxyResolver(registry=RegistryManager(allowed_uris=["files.text.read"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("example", encoding="utf-8")
            response = resolver.execute("files.text.read", {"path": str(path)})
            self.assertEqual(response["status"], "SUCCESS")
            denied = resolver.execute("files.text.atomic_edit", {"path": str(path), "action": "delete"})
            self.assertEqual(denied["status"], "ERROR")

    def test_coder_entrypoint_invokes_existing_desktop(self):
        with patch("hornet.desktop.app.main", return_value="launched") as launch:
            self.assertEqual(coder.main(), "launched")
        launch.assert_called_once_with()

    def test_model_interfaces_are_existing_functions(self):
        from hornet.generation import chat as existing_chat
        from hornet.desktop.workspace import discover_models as existing_discovery
        self.assertIs(chat, existing_chat)
        self.assertIs(discover_models, existing_discovery)


if __name__ == "__main__":
    unittest.main()

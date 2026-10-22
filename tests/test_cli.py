"""Regressions for the delivered CLI audit, using disposable projects only."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from hornet.cli import main
from hornet import __version__
from hornet.project import HornetError, Project


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hornet-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"HORNET_ROOT": "", "HORNET_PATH": "", "HORNET_MODEL": "", "HORNET_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.call("init", "--name", "audit", "--path", str(self.root))

    def call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--root", str(self.root), "--json", *args])
        self.assertEqual(code, expected, (out.getvalue(), err.getvalue()))
        self.assertEqual(err.getvalue(), "")
        return json.loads(out.getvalue())

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def goal(self, name, state="COMPLETED"):
        self.put("continue/goals/" + name + "/state", {"status": state})
        return self.root / "continue/goals" / name

    def contract(self, rule):
        self.put("contract.json", {"target_files": rule})

    def test_init_preserves_readme_and_recipes(self):
        self.put("README.md", "USER CONTENT")
        self.put("recipes/article.json", "USER RECIPE")
        result = self.call("init", "--name", "audit", "--path", str(self.root))
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "USER CONTENT")
        self.assertEqual((self.root / "recipes/article.json").read_text(encoding="utf-8"), "USER RECIPE")
        self.assertEqual(result["created"], [])

    def test_init_refuses_different_name(self):
        self.call("init", "--name", "different", "--path", str(self.root), expected=2)

    def test_init_preflights_file_conflict(self):
        other = self.root / "conflict"
        other.mkdir()
        (other / "recipes").write_text("preserve", encoding="utf-8")
        self.call("init", "--name", "conflict", "--path", str(other), expected=2)
        self.assertFalse((other / "README.md").exists())

    def test_status_empty(self):
        metrics = self.call("status")["metrics"]
        self.assertEqual(metrics["active_goals"], 0)
        self.assertEqual(metrics["total_pointers"], 0)

    def test_status_real_goals_and_pointer_details(self):
        self.goal("working", "IN_PROGRESS")
        self.goal("done")
        self.put("continue/goals/notes/state", "- Current Progress: still investigating\n")
        self.put("continue/pointers/ptr_a.json", {"pointer_id": "ptr_a", "status": "LOCKED", "owner": "test", "current_node": "continue/goals/working"})
        self.put("continue/pointers/ptr_b.json", {"status": "IDLE", "owner": None})
        result = self.call("status", "--verbose")
        self.assertEqual(result["metrics"]["active_goals"], 1)
        self.assertEqual(result["metrics"]["completed_goals"], 1)
        self.assertEqual(result["metrics"]["unknown_goals"], 1)
        self.assertEqual(result["metrics"]["active_pointers"], 1)
        self.assertEqual(len(result["pointers"]), 2)

    def test_malformed_pointer_is_error(self):
        self.put("continue/pointers/bad.json", "{broken")
        self.call("status", expected=2)

    def test_root_discovery_from_nested_directory(self):
        nested = self.root / "nested"
        nested.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(nested)
            self.assertEqual(Project().root, self.root.resolve())
        finally:
            os.chdir(previous)

    def test_common_flags_after_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["status", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["root"], str(self.root.resolve()))

    def test_argument_errors_are_json(self):
        self.assertFalse(self.call("verify", expected=2)["ok"])
        self.assertFalse(self.call("not-a-command", expected=2)["ok"])

    def test_verification_success_hash_and_absent_target(self):
        self.put("target.txt", "VICTORY ALWAYS\n")
        digest = hashlib.sha256(b"VICTORY ALWAYS\n").hexdigest()
        self.contract({"target.txt": {"contains": ["VICTORY"], "not_contains": ["FAIL"], "sha256": digest, "min_size": 5}, "absent.txt": {"exists": False}})
        self.assertTrue(self.call("verify", "--contract", "contract.json")["ok"])

    def test_verification_failure_is_exit_one(self):
        self.contract({"missing.txt": {"exists": True}})
        self.assertFalse(self.call("verify", "--contract", "contract.json", expected=1)["ok"])

    def test_missing_contract_is_exit_two(self):
        self.call("verify", "--contract", "missing.json", expected=2)

    def test_invalid_contracts(self):
        for rule in ({}, {"x": {"contains": ["["]}}, {"x": {"exists": "false"}}, {"x": {"min_size": -1}}, {"x": {"min_size": 4, "max_size": 2}}, {"x": {"sha256": "invalid"}}, {"x": {"execute": "anything"}}):
            self.contract(rule)
            self.call("verify", "--contract", "contract.json", expected=2)

    def test_contract_paths_are_project_relative(self):
        self.put("target.txt", "hello")
        self.put("nested/contract.json", {"target_files": {"target.txt": {"contains": ["hello"]}}})
        self.call("verify", "--contract", "nested/contract.json")

    def test_contract_escape_refused(self):
        self.contract({"../outside.txt": {"exists": False}})
        self.call("verify", "--contract", "contract.json", expected=2)

    def test_binary_contract_hash_supported(self):
        (self.root / "binary").write_bytes(b"\xff\x00")
        self.contract({"binary": {"sha256": hashlib.sha256(b"\xff\x00").hexdigest()}})
        self.call("verify", "--contract", "contract.json")

    def test_archive_dry_run_and_move(self):
        node = self.goal("done")
        before = (node / "state").read_bytes()
        result = self.call("archive", "--all", "--dry-run")
        self.assertTrue(node.exists())
        self.assertEqual(len(result["moves"]), 1)
        result = self.call("archive", "--node", "done")
        self.assertFalse(node.exists())
        self.assertEqual(result["archived_count"], 1)
        self.assertEqual((self.root / result["moves"][0]["destination"] / "state").read_bytes(), before)

    def test_archive_never_substring_matches_success(self):
        self.goal("failed", "unsuccessful")
        self.call("archive", "--node", "failed", expected=2)
        self.assertEqual(self.call("archive", "--all")["moves"], [])

    def test_archive_checks_entire_plan_before_moving(self):
        first = self.goal("a")
        self.goal("z")
        self.put("continue/goals/z/lock", "owned")
        self.call("archive", "--all", expected=2)
        self.assertTrue(first.exists())

    def test_archive_rejects_active_historical_pointer(self):
        self.goal("done")
        self.put("continue/pointers/ptr.json", {"status": "LOCKED", "current_node": "/home/pete/dev/hornet/continue/goals/done/cells/cell1"})
        self.call("archive", "--node", "done", expected=2)

    def test_archive_operation_lock(self):
        self.goal("done")
        self.put(".hornet-operation.lock", "existing")
        self.call("archive", "--all", expected=2)
        self.assertEqual((self.root / ".hornet-operation.lock").read_text(encoding="utf-8"), "existing")

    def test_nested_cell_lock_blocks_archival(self):
        self.goal("done")
        self.put("continue/goals/done/cells/child/lock", "held")
        self.call("archive", "--node", "done", expected=2)

    def test_unassigned_active_pointer_blocks_archival(self):
        self.goal("done")
        self.put("continue/pointers/ptr.json", {"status": "LOCKED", "current_node": None})
        self.call("archive", "--node", "done", expected=2)

    def test_bracketed_legacy_state(self):
        self.put("continue/goals/legacy/state", "[INIT] Created\n[STATUS] IMPLEMENTED_AND_VERIFIED\n[ITERATION] 1\n")
        result = self.call("status")
        self.assertEqual(result["goals"][0]["status"], "IMPLEMENTED_AND_VERIFIED")
        self.assertEqual(result["metrics"]["unknown_goals"], 1)

    def test_literal_newlines_in_legacy_state(self):
        self.put("continue/goals/legacy/state", r"Iteration: 1\nStatus: INITIALIZED\nNext Action: Investigate")
        self.assertEqual(self.call("status")["metrics"]["active_goals"], 1)

    def test_malformed_state_json_is_error(self):
        self.put("continue/goals/bad/state", '{"status": invalid}')
        self.call("status", expected=2)

    def test_unknown_pointer_state_blocks_archival(self):
        self.goal("done")
        self.put("continue/pointers/ptr.json", {"status": "UNRESOLVED"})
        self.call("archive", "--node", "done", expected=2)

    def test_relative_and_normalized_pointer_paths_block_archival(self):
        self.goal("done")
        for current in ("done/cells/child", "continue/goals/other/../done", "./continue/goals/done"):
            self.put("continue/pointers/ptr.json", {"status": "LOCKED", "current_node": current})
            self.call("archive", "--node", "done", expected=2)

    def test_archive_rollback_on_second_move_failure(self):
        a, b = self.goal("a"), self.goal("b")
        original = os.rename
        def rename(src, dst):
            if Path(src) == b:
                raise OSError("synthetic move failure")
            return original(src, dst)
        with patch("hornet.archival.os.rename", side_effect=rename):
            self.call("archive", "--all", expected=2)
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())

    def test_archive_preserves_existing_destination(self):
        self.put("archive/v9/completed/done/state", "old")
        self.goal("done")
        self.call("archive", "--all")
        self.assertEqual((self.root / "archive/v9/completed/done/state").read_text(encoding="utf-8"), "old")

    def test_recipe_list_show_and_unknown(self):
        self.assertEqual(self.call("recipe", "list")["recipes"][0]["name"], "article")
        self.assertEqual(self.call("recipe", "show", "article")["recipe"]["name"], "article")
        self.call("recipe", "show", "not-found", expected=2)

    def test_recipe_custom_pipeline_and_path(self):
        recipe = {"name": "custom", "prompt": "Discuss {topic}", "template": "{topic}\u200b  \n", "steps": ["generate_article", "smash_patterns"]}
        self.put("recipes/custom.json", recipe)
        result = self.call("generate", "--recipe", "recipes/custom.json", "--provider", "template", "--topic", "Proof")
        self.assertEqual(Path(result["output"]).read_text(encoding="utf-8"), "Proof\n")

    def test_invalid_recipe_actions_and_placeholders(self):
        for recipe in ({"name": "bad", "prompt": "{topic.__class__}"}, {"name": "bad", "prompt": "topic", "steps": ["shell"]}, {"name": "bad", "prompt": "topic", "steps": [{"action": []}]}, {"name": "bad", "prompt": "{topic}", "steps": ["generate_article", "generate_article"]}):
            self.put("recipes/bad.json", recipe)
            self.call("generate", "--recipe", "bad", "--provider", "template", "--topic", "x", expected=2)

    def test_generation_default_is_bounded_missing_model_error(self):
        self.call("generate", "--topic", "Test", expected=2)
        self.assertFalse((self.root / "output/article_Test.md").exists())

    def test_offline_outline_and_no_overwrite(self):
        result = self.call("generate", "--provider", "template", "--topic", "Restore drills")
        self.assertEqual(result["kind"], "offline_outline")
        self.assertIn("no language model was used", Path(result["output"]).read_text(encoding="utf-8"))
        self.call("generate", "--provider", "template", "--topic", "Restore drills", expected=2)
        self.call("generate", "--provider", "template", "--topic", "Restore drills", "--force")

    def test_topic_cannot_escape_output_directory(self):
        result = self.call("generate", "--provider", "template", "--topic", "../../outside/")
        self.assertEqual(Path(result["output"]).parent, self.root / "output")

    def test_smash_preserves_input_and_punctuation(self):
        source = self.put("draft.txt", "Hello\u200b, world!  \n\nNext line.\n")
        result = self.call("smash", "--input", "draft.txt")
        self.assertEqual(Path(result["output"]).read_text(encoding="utf-8"), "Hello, world!\n\nNext line.\n")
        self.assertIn("\u200b", source.read_text(encoding="utf-8"))
        self.call("smash", "--input", "draft.txt", "--output", "draft.txt", "--force", expected=2)

    def test_missing_smash_input_error(self):
        self.call("smash", "--input", "missing.txt", expected=2)

    def test_outputs_refuse_escape(self):
        self.call("generate", "--provider", "template", "--topic", "x", "--output", "../escape.md", expected=2)

    def test_symlink_paths_refused(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest("OS does not permit symlink creation: " + str(exc))
        self.call("generate", "--provider", "template", "--topic", "x", "--output", "linked/out.md", expected=2)
        self.goal("linked-goal")
        (self.root / "continue/goals/linked-goal/linked").symlink_to(outside, target_is_directory=True)
        self.call("archive", "--node", "linked-goal", expected=2)

    @contextmanager
    def model_server(self, body, status=200, headers=None):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append({"path": self.path, "headers": dict(self.headers), "data": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode("utf-8"))
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:{}/v1".format(server.server_port), seen
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_model_generation_against_local_fixture(self):
        with self.model_server({"choices": [{"message": {"content": "A model-produced test draft."}, "finish_reason": "stop"}]}) as (base, seen):
            result = self.call("generate", "--topic", "Recovery", "--model", "fixture", "--base-url", base)
        self.assertEqual(result["kind"], "model_draft")
        self.assertEqual(Path(result["output"]).read_text(encoding="utf-8"), "A model-produced test draft.")
        self.assertEqual(seen[0]["path"], "/v1/chat/completions")
        self.assertEqual(seen[0]["data"]["model"], "fixture")
        self.assertFalse(seen[0]["data"]["stream"])

    def test_model_truncation_malformed_and_http_errors(self):
        cases = [({"choices": [{"message": {"content": "incomplete"}, "finish_reason": "length"}]}, 200), ({"wrong": "response"}, 200), ({"error": "secret should not be echoed"}, 401)]
        for body, code in cases:
            with self.model_server(body, status=code) as (base, _):
                result = self.call("generate", "--topic", "x", "--model", "fixture", "--base-url", base, expected=2)
            self.assertNotIn("secret should not be echoed", result["error"])
            self.assertFalse((self.root / "output/article_x.md").exists())

    def test_model_redirect_not_followed(self):
        with self.model_server({}, status=307, headers={"Location": "http://127.0.0.1:1/forbidden"}) as (base, seen):
            result = self.call("generate", "--topic", "x", "--model", "fixture", "--base-url", base, expected=2)
        self.assertIn("307", result["error"])
        self.assertEqual(len(seen), 1)

    def test_model_credentials_not_sent_over_remote_http(self):
        with patch.dict(os.environ, {"HORNET_API_KEY": "fixture-secret"}):
            result = self.call("generate", "--topic", "x", "--model", "fixture", "--base-url", "http://example.invalid/v1", expected=2)
        self.assertNotIn("fixture-secret", str(result))

    def test_module_entry_and_process_exit_codes(self):
        result = subprocess.run([sys.executable, "-m", "hornet", "--version"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn(__version__, result.stdout)
        result = subprocess.run([sys.executable, "-m", "hornet", "--root", str(self.root), "smash", "--input", "absent"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()

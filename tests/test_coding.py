"""Tool boundary/controller regressions; all local files are disposable."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from hornet.coding.agent import work, validate_call, tool_result_content
from hornet.coding.worker import Worker, WorkerError
from hornet.coding.model import completion
from hornet.coding.transport import SSHWorker
from hornet.project import HornetError, Project


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hornet-worker-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = Worker(self.root)

    def test_create_read_update_with_sha(self):
        first = self.worker.write_file("a.py", "print('hello')\n", "")
        read = self.worker.read_file("a.py")
        self.assertEqual(first["sha256"], read["sha256"])
        self.assertIn("1: print", read["content"])
        self.worker.write_file("a.py", "print('next')\n", read["sha256"])
        with self.assertRaises(WorkerError):
            self.worker.write_file("a.py", "overwrite", first["sha256"])

    def test_paths_block_metadata_and_escape(self):
        for name in ("../escape", "/etc/passwd", ".git/config", "nested/../../escape"):
            with self.assertRaises(WorkerError):
                self.worker.path(name)

    def test_symlink_blocked(self):
        directory = self.root / "real"
        directory.mkdir()
        try:
            (self.root / "link").symlink_to(directory, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaises(WorkerError):
            self.worker.write_file("link/a", "data", "")

    def test_search_and_list(self):
        self.worker.write_file("a.py", "Victory Always\n", "")
        self.assertEqual(self.worker.search("Victory")["matches"][0]["line"], 1)
        self.assertEqual(self.worker.list_files()["files"][0]["path"], "a.py")

    def test_shell_executables_refused(self):
        for command in (["bash", "-c", "anything"], ["git", "push"], ["ssh", "other-host"]):
            with self.assertRaises(WorkerError):
                self.worker.run_command(command)

    def test_size_limits(self):
        with self.assertRaises(WorkerError):
            self.worker.write_file("large", "x" * (256 * 1024 + 1), "")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hornet-controller-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(task="Implement a test ticket", task_file=None, model="fixture",
                base_url="http://127.0.0.1:1234/v1", max_tokens=4096, timeout=180,
                max_turns=3, max_tools=5, worker_config="unused.json", run_dir="runs")

    def fake_worker(self, tests=0, uid=1000, changed=True):
        snapshots = [0]
        class Fake:
            def call(self, name, arguments=None):
                if name == "status":
                    return {"ok": True, "ready": True, "platform": "linux", "uid": uid}
                if name == "run_tests":
                    return {"ok": tests == 0, "exit_code": tests, "output": "fixture test results"}
                if name == "snapshot":
                    return {"ok": True, "binding": {"fixture": "worker"}, "files": {}}
                if name == "changes":
                    snapshots[0] += 1
                    return {"ok": True, "files": [{"path": "doctor.py", "content": "fixture"}] if changed and snapshots[0] > 1 else []}
                return {"ok": True}
        return Fake()

    def test_final_test_gate_and_review_export(self):
        def model(*args):
            return {"role": "assistant", "content": "Finished"}, {}
        result = work(Project(self.root), self.args, self.fake_worker(), model)
        self.assertEqual(result["status"], "ready_for_review")
        self.assertFalse(result["integrated"])
        self.assertTrue((Path(result["run_dir"]) / "changes.json").exists())
        progress = json.loads((Path(result["run_dir"]) / "progress.json").read_text())
        self.assertEqual(progress["stage"], "ready_for_review")

    def test_failed_tests_and_no_changes_do_not_pass(self):
        for worker in (self.fake_worker(tests=1), self.fake_worker(changed=False)):
            result = work(Project(self.root), self.args, worker, lambda *args: ({"role": "assistant", "content": "Done"}, {}))
            self.assertFalse(result["ok"])

    def test_existing_edits_alone_do_not_count_as_new_work(self):
        worker = self.fake_worker()
        original = worker.call
        def call(name, arguments=None):
            if name == "changes":
                return {"ok": True, "files": [{"path": "old.py", "content": "already changed"}]}
            return original(name, arguments)
        worker.call = call
        result = work(Project(self.root), self.args, worker, lambda *args: ({"role": "assistant", "content": "Done"}, {}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["changed_files"], [])
        self.assertTrue((Path(result["run_dir"]) / "initial-changes.json").exists())

    def test_root_worker_refused(self):
        with self.assertRaises(HornetError):
            work(Project(self.root), self.args, self.fake_worker(uid=0), None)

    def test_unknown_and_malformed_tool_requests_refused(self):
        for call in ({"id": "a", "type": "function", "function": {"name": "shell", "arguments": "{}"}},
                     {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "[]"}}):
            with self.assertRaises(HornetError):
                validate_call(call)

    def test_turn_budget_stops_loop(self):
        counter = [0]
        def model(*args):
            counter[0] += 1
            return {"role": "assistant", "content": "", "tool_calls": [{"id": str(counter[0]), "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]}, {}
        result = work(Project(self.root), self.args, self.fake_worker(), model)
        self.assertFalse(result["ok"])
        self.assertIn("turn budget", result["error"])

    def test_tool_budget_stops_before_execution(self):
        self.args.max_tools = 1
        def model(*args):
            calls = [{"id": str(i), "type": "function", "function": {"name": "list_files", "arguments": "{}"}} for i in range(2)]
            return {"role": "assistant", "content": "", "tool_calls": calls}, {}
        result = work(Project(self.root), self.args, self.fake_worker(), model)
        self.assertIn("tool-operation budget", result["error"])

    def test_interrupt_records_cancelled_result_and_releases_lock(self):
        def interrupted(*args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            work(Project(self.root), self.args, self.fake_worker(), interrupted)
        self.assertFalse((self.root / "unused.json.run.lock").exists())
        run = next((self.root / "runs").iterdir())
        self.assertEqual(json.loads((run / "result.json").read_text())["status"], "cancelled")

    def test_selected_tools_are_offered_and_unoffered_calls_refused(self):
        self.args.tools = ["read_file", "edit_file"]
        def model(messages, tools, *args):
            self.assertEqual({t["function"]["name"] for t in tools}, {"read_file", "edit_file"})
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "x", "type": "function", "function": {"name": "run_command", "arguments": '{"argv":["python3"]}'}}]}, {}
        result = work(Project(self.root), self.args, self.fake_worker(), model)
        self.assertFalse(result["ok"])
        self.assertIn("not offered", result["error"])


class ModelHistoryTests(unittest.TestCase):
    def response(self, message, finish="tool_calls"):
        opener = MagicMock()
        body = json.dumps({"choices": [{"message": message, "finish_reason": finish}]}).encode()
        opener.open.return_value.__enter__.return_value.read.return_value = body
        return opener

    def call(self, message, finish="tool_calls"):
        opener = self.response(message, finish)
        with patch("hornet.coding.model.request.build_opener", return_value=opener):
            result = completion([], [], "fixture", "http://127.0.0.1:1234/v1")
        sent = json.loads(opener.open.call_args[0][0].data)
        self.assertNotIn("temperature", sent)
        return result

    def test_tool_history_preserves_opaque_reasoning_state(self):
        call = {"id": "a", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
        message, _ = self.call({"content": "", "tool_calls": [call], "reasoning_content": "opaque fixture"})
        self.assertEqual(message["reasoning_content"], "opaque fixture")
        self.assertEqual(message["tool_calls"], [call])

    def test_final_message_omits_reasoning_state(self):
        message, _ = self.call({"content": "Done", "reasoning_content": "opaque fixture"}, "stop")
        self.assertNotIn("reasoning_content", message)

    def test_invalid_reasoning_state_and_truncated_calls_refused(self):
        call = {"id": "a", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
        with self.assertRaises(HornetError):
            self.call({"content": "", "tool_calls": [call], "reasoning_content": {"invalid": True}})
        with self.assertRaises(HornetError):
            self.call({"content": "", "tool_calls": [call]}, "length")


class ToolResultFormattingTests(unittest.TestCase):
    def test_source_quotes_and_backslashes_are_not_double_escaped(self):
        source = '1: """A docstring."""\n2: text = "\\n"\n'
        result = {"ok": True, "path": "hornet/a.py", "sha256": "a" * 64, "content": source}
        rendered = tool_result_content("read_file", result)
        self.assertTrue(rendered.endswith(source))
        metadata = json.loads(rendered.split("\n\n", 1)[0])
        self.assertEqual(metadata["sha256"], "a" * 64)
        self.assertNotIn("content", metadata)

    def test_failed_command_traceback_stays_readable(self):
        output = 'Traceback:\n  raise ValueError("fixture")\n'
        rendered = tool_result_content("run_tests", {"ok": False, "exit_code": 1, "output": output})
        self.assertTrue(rendered.endswith(output))
        self.assertEqual(json.loads(rendered.split("\n\n", 1)[0])["exit_code"], 1)

    def test_other_results_and_read_errors_remain_json(self):
        result = {"ok": False, "error": "Missing file"}
        self.assertEqual(json.loads(tool_result_content("read_file", result)), result)
        result = {"ok": True, "sha256": "b" * 64}
        self.assertEqual(json.loads(tool_result_content("write_file", result)), result)


class SmallEditTransportTests(unittest.TestCase):
    def worker(self):
        worker = SSHWorker.__new__(SSHWorker)
        worker._rpc = MagicMock(return_value={"ok": True, "output": '{"ok": true, "changed": true}'})
        return worker

    def arguments(self):
        return {"path": "hornet/a.py", "old_text": "return a - b", "new_text": "return a + b", "expected_sha256": "a" * 64}

    def test_edit_runs_fixed_guest_program_with_data_argv(self):
        worker = self.worker()
        args = self.arguments()
        result = worker.call("edit_file", args)
        self.assertTrue(result["ok"])
        name, payload = worker._rpc.call_args[0]
        self.assertEqual(name, "run_command")
        self.assertEqual(payload["argv"][:2], ["python3", "-c"])
        self.assertEqual(json.loads(payload["argv"][3]), args)
        self.assertIn("/opt/hornet-worker/worker.py", payload["argv"][2])

    def test_invalid_and_oversized_edits_do_not_call_guest(self):
        worker = self.worker()
        for args in ({}, dict(self.arguments(), old_text=""), dict(self.arguments(), expected_sha256="bad"),
                     dict(self.arguments(), new_text="x" * 4000), dict(self.arguments(), new_text=self.arguments()["old_text"])):
            self.assertFalse(worker.call("edit_file", args)["ok"])
        worker._rpc.assert_not_called()

    def test_edit_failures_and_invalid_responses(self):
        worker = self.worker()
        worker._rpc.return_value = {"ok": True, "output": '{"ok": false, "error": "hash mismatch"}'}
        self.assertFalse(worker.call("edit_file", self.arguments())["ok"])
        worker._rpc.return_value = {"ok": True, "output": "invalid JSON"}
        with self.assertRaises(HornetError):
            worker.call("edit_file", self.arguments())

    def test_python_tool_constructs_argv_without_model_arrays(self):
        worker = self.worker()
        code = 'print("quoted", 2 + 3)'
        worker.call("run_python", {"code": code})
        self.assertEqual(worker._rpc.call_args[0], ("run_command", {"argv": ["python3", "-c", code]}))
        worker._rpc.reset_mock()
        self.assertFalse(worker.call("run_python", {"code": ""})["ok"])
        self.assertFalse(worker.call("run_python", {"code": "x" * 4001})["ok"])
        worker._rpc.assert_not_called()

    def test_read_defaults_to_small_range_without_mutating_arguments(self):
        worker = self.worker()
        args = {"path": "a.py"}
        worker.call("read_file", args)
        self.assertEqual(worker._rpc.call_args[0], ("read_file", {"path": "a.py", "max_lines": 80}))
        self.assertEqual(args, {"path": "a.py"})
        worker.call("read_file", {"path": "a.py", "max_lines": 20})
        self.assertEqual(worker._rpc.call_args[0][1]["max_lines"], 20)

    def test_final_suite_uses_fresh_import_cache_in_the_guest(self):
        worker = self.worker()
        worker.call('run_tests')
        name, payload = worker._rpc.call_args[0]
        self.assertEqual(name, 'run_command')
        self.assertEqual(payload['argv'][:3], ['python3', '-I', '-c'])
        self.assertIn('sys.pycache_prefix = cache', payload['argv'][3])
        self.assertIn("discover('tests')", payload['argv'][3])
        self.assertEqual(payload['timeout'], 120)


if __name__ == "__main__":
    unittest.main()

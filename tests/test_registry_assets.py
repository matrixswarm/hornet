"""Offline regression checks; every mutation stays in a temporary directory."""
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hornet.registry.registry_manager import RegistryManager
from hornet.registry.proxy_resolver import ProxyResolver
from hornet.registry.registry_mcp_server import RegistryMCPServer

HERE = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.registry = RegistryManager()
        self.proxy = ProxyResolver(registry=self.registry)
        self.manifest = self.directory / "manifest.json"
        self.manifest.write_text('{"version":"1", "bots":[{"status":"old"}], "nullable":null}', encoding="utf-8")
        self.source = self.directory / "sample.py"
        self.source.write_text("class First:\n    def untouched(self):\n        pass\n\nclass Basic:\n    def execute(self, a, b):\n        pass\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, uri, args, status="SUCCESS"):
        args = dict(args)
        path = args.get("file_path", args.get("path"))
        if path and Path(path).is_file() and "expected_sha256" in self.registry.details(uri)["inputSchema"]["properties"]:
            args.setdefault("expected_sha256", hashlib.sha256(Path(path).read_bytes()).hexdigest())
        result = self.proxy.execute(uri, args)
        self.assertEqual(result["status"], status, result)
        return result["proxy_response"]

    def test_catalog_and_all_aliases(self):
        self.assertEqual(self.registry.doctor()["issues"], [])
        for alias, name in self.registry.aliases.items():
            self.assertEqual(self.registry.details(alias)["uri"], name)

    def test_cell_allowlist_applies_to_search_and_execution(self):
        registry = RegistryManager(allowed_uris=["mcp.json_query"])
        self.assertEqual([a["uri"] for a in registry.find("")], ["data.json.query"])
        with self.assertRaises(ValueError):
            registry.details("mcp.shell")
        result = ProxyResolver(registry=registry).execute("mcp.shell", {"command":"echo denied"})
        self.assertEqual(result["status"], "ERROR")
        self.assertFalse(result["was_proxied"])
        with self.assertRaises(ValueError):
            RegistryManager(allowed_uris=["invented.asset"])

    def test_dedicated_read_has_no_write_action(self):
        self.call("files.text.read", {"path":str(self.source)})
        self.call("files.text.read", {"path":str(self.source), "action":"replace_line"}, "ERROR")

    def test_or_search(self):
        names = [a["uri"] for a in self.registry.find("JSON utterly_nonexistent", 50)]
        self.assertIn("data.json.query", names)

    def test_capability_search_splits_label_separators(self):
        for query in ("method_body", "method-body", "method.body", "method body"):
            with self.subTest(query=query):
                names = [a["uri"] for a in self.registry.find(query, 3)]
                self.assertIn("languages.coding.python.method.fill", names)
                self.assertNotIn("data.json.edit", names)
        for query in ("data.json.edit", "mcp.atomic_body"):
            self.assertEqual(self.registry.find(query, 1)[0]['uri'], self.registry.details(query)['uri'])

    def test_every_tool_example(self):
        for asset in self.registry.assets.values():
            if asset["type"] != "TOOL":
                continue
            with self.subTest(uri=asset["uri"]):
                self.set_example_files()
                if "design_pattern" in asset["uri"]:
                    (self.directory / "strategy.py").write_text("class Contract:\n    pass\n", encoding="utf-8")
                if asset["uri"] == "data.json.ordered_edit":
                    self.manifest.write_text('{"version":"1"}', encoding="utf-8")
                args = json.loads(json.dumps(asset["examples"][0]))
                for key in ("path", "file_path"):
                    if key in args:
                        args[key] = str(self.directory / Path(args[key]).name)
                result = self.proxy.execute(asset["uri"], args)
                self.assertIn(result["status"], ("SUCCESS", "STAGED"), result)

    def set_example_files(self):
        (self.directory / "example.py").write_text("LIMIT = 1\n", encoding="utf-8")
        (self.directory / "basic.py").write_text("class Basic:\n    def execute(self, a, b):\n        pass\n", encoding="utf-8")
        # Pattern examples require an existing target; interface examples refuse overwrite.
        (self.directory / "strategy.py").unlink(missing_ok=True)
        self.manifest.write_text('{"version":"1", "bots":[{"status":"old"}], "status":"pending"}', encoding="utf-8")

    def test_unknown_uri_and_extra_arguments(self):
        self.call("invented.tool", {}, "ERROR")
        self.call("data.json.query", {"file": str(self.manifest)}, "ERROR")
        self.call("data.json.query", {"file_path": str(self.manifest), "surprise": 1}, "ERROR")
        self.call("runtime.shell.execute", {"command": "echo ok", "timeout": True}, "ERROR")

    def test_unread_commit_refused_by_real_registry(self):
        result = self.proxy.execute("data.json.edit", {"file_path": str(self.manifest), "target_path": "version", "value": "2"})
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("Read the target first", result["proxy_response"]["message"])

    def test_read_null_missing_and_array(self):
        self.assertIsNone(self.call("data.json.query", {"file_path": str(self.manifest), "target_path": "nullable"})["value"])
        self.call("data.json.query", {"file_path": str(self.manifest), "target_path": "absent"}, "ERROR")
        self.assertEqual(self.call("data.json.query", {"file_path": str(self.manifest), "target_path": "bots[0].status"})["value"], "old")
        self.call("data.json.query", {"file_path": str(self.manifest), "target_path": "bots[-1]"}, "ERROR")

    def test_json_edit_and_alias(self):
        self.call("mcp.json_surgical_modification", {"file_path": str(self.manifest), "target_path": "bots[0].status", "value": "ready"})
        self.assertEqual(json.loads(self.manifest.read_text())["bots"][0]["status"], "ready")

    def test_empty_file_read_has_hash(self):
        self.source.write_bytes(b"")
        result = self.call("files.text.lines", {"path":str(self.source), "action":"read_lines"})
        self.assertEqual(result["text"], "")
        self.assertEqual(result["source_sha256"], hashlib.sha256(b"").hexdigest())

    def test_array_move_destination_after_deletion(self):
        self.manifest.write_text('{"items":["a","b","c"]}')
        self.call("data.json.move", {"file_path":str(self.manifest),"migration_type":"move", "source_path":"items[0]", "destination_path":"items[2]"})
        self.assertEqual(json.loads(self.manifest.read_text())["items"], ["b","c","a"])

    def test_atomic_batch_abort(self):
        before = self.manifest.read_bytes()
        self.call("data.json.batch", {"file_path": str(self.manifest), "operations": [
            {"action": "set", "target_path": "version", "value": "2"},
            {"action": "delete", "target_path": "absent"}]}, "ERROR")
        self.assertEqual(before, self.manifest.read_bytes())
        self.assertEqual(list(self.directory.glob("*.atomic")), [])

    def test_no_scalar_destruction_or_sparse_arrays(self):
        before = self.manifest.read_bytes()
        for path in ("version.child", "bots[50]", "bots[-1]", "bots[0].status.child"):
            self.call("data.json.edit", {"file_path": str(self.manifest), "target_path": path, "value": 5}, "ERROR")
            self.assertEqual(before, self.manifest.read_bytes())

    def test_null_move(self):
        self.call("data.json.move", {"file_path": str(self.manifest), "migration_type": "rename", "source_path": "nullable", "destination_path": "renamed"})
        data = json.loads(self.manifest.read_text())
        self.assertIsNone(data["renamed"])
        self.assertNotIn("nullable", data)

    def test_move_overlap_and_overwrite(self):
        before = self.manifest.read_bytes()
        for src, dst in (("bots", "bots.child"), ("bots[0]", "bots"), ("version", "nullable")):
            self.call("data.json.move", {"file_path": str(self.manifest), "migration_type": "move", "source_path": src, "destination_path": dst}, "ERROR")
            self.assertEqual(before, self.manifest.read_bytes())

    def test_shape_failure_is_error(self):
        self.call("data.json.validate_shape", {"file_path": str(self.manifest), "schema": {"bots": "int"}}, "ERROR")
        self.call("data.json.validate_shape", {"file_path": str(self.manifest), "schema": {"bots": "unknown"}}, "ERROR")

    def test_ordered_insert_and_missing_delete(self):
        args = {"path": str(self.manifest), "op": "INSERT_AFTER", "anchor": "version", "node": "new", "value": 7}
        self.call("data.json.ordered_edit", args)
        self.assertEqual(list(json.loads(self.manifest.read_text()))[:2], ["version", "new"])
        self.call("data.json.ordered_edit", args, "ERROR")
        self.call("data.json.ordered_edit", {"path": str(self.manifest), "op": "DELETE", "node": "absent"}, "ERROR")

    def test_preview_and_compare_then_commit(self):
        before = self.manifest.read_bytes()
        args = {"file_path": str(self.manifest), "target_path": "version", "value": "2", "commit": False}
        staged = self.call("data.json.edit", args, "STAGED")
        self.assertEqual(before, self.manifest.read_bytes())
        self.assertTrue(Path(staged["shadow_path"]).exists())
        args.update(commit=True, expected_sha256=staged["source_sha256"])
        self.call("data.json.edit", args)
        self.call("data.json.edit", args, "ERROR")

    def test_unique_staging_paths(self):
        args = {"file_path": str(self.manifest), "target_path": "version", "value": "2", "commit": False}
        a = self.call("data.json.edit", args, "STAGED")
        b = self.call("data.json.edit", args, "STAGED")
        self.assertNotEqual(a["shadow_path"], b["shadow_path"])

    def test_locked_file_refuses_and_keeps_lock(self):
        lock = Path(str(self.manifest) + ".hornet.lock")
        lock.write_text("other owner")
        self.call("data.json.edit", {"file_path": str(self.manifest), "target_path": "version", "value": "2"}, "ERROR")
        self.assertTrue(lock.exists())

    def test_two_writers_one_source_hash(self):
        before = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        def edit(value):
            return self.proxy.execute("data.json.edit", {"file_path": str(self.manifest), "target_path": "version", "value": value, "expected_sha256": before})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ["two", "three"]))
        self.assertEqual(sum(r["status"] == "SUCCESS" for r in results), 1)
        self.assertFalse(Path(str(self.manifest) + ".hornet.lock").exists())

    def test_body_exact_target_and_nested_logic(self):
        self.call("languages.coding.python.method.fill", {"path": str(self.source), "class_name": "Basic", "method_name": "execute", "logic": "if a < 0:\n    return 0\nreturn a + b"})
        text = self.source.read_text()
        self.assertIn("def untouched(self):\n        pass", text)
        scope = {}
        exec(compile(text, str(self.source), "exec"), scope)
        self.assertEqual(scope["Basic"]().execute(-1, 2), 0)
        self.assertEqual(scope["Basic"]().execute(2, 3), 5)

    def test_ambiguous_method_and_invalid_syntax(self):
        self.source.write_text("class A:\n    def run(self):\n        pass\nclass B:\n    def run(self):\n        pass\n")
        before = self.source.read_bytes()
        self.call("languages.coding.python.method.fill", {"path": str(self.source), "method_name": "run", "logic": "return 1"}, "ERROR")
        self.call("languages.coding.python.method.fill", {"path": str(self.source), "class_name": "A", "method_name": "run", "logic": "if ???"}, "ERROR")
        self.assertEqual(before, self.source.read_bytes())

    def test_method_body_rejects_accidental_signature_and_allows_helpers(self):
        before = self.source.read_bytes()
        result = self.call('languages.coding.python.method.fill', {
            'path': str(self.source), 'class_name': 'Basic', 'method_name': 'execute',
            'logic': 'def execute(self, a, b):\n    return a + b'}, 'ERROR')
        self.assertIn('only the method body', result['message'])
        self.assertEqual(self.source.read_bytes(), before)
        self.call('languages.coding.python.method.fill', {
            'path': str(self.source), 'class_name': 'Basic', 'method_name': 'execute',
            'logic': 'def helper(x):\n    return x * 2\nreturn helper(a) + b'})
        scope = {}
        exec(compile(self.source.read_text(), str(self.source), 'exec'), scope)
        self.assertEqual(scope['Basic']().execute(3, 4), 10)

    def test_multiline_signature(self):
        self.source.write_text("def run(\n    x,\n    y,\n):\n    pass\n")
        self.call("languages.coding.python.method.fill", {"path": str(self.source), "method_name": "run", "logic": "return x + y"})
        ast.parse(self.source.read_text())

    def test_interface_empty_valid_and_overwrite_refusal(self):
        target = self.directory / "contract.py"
        args = {"path": str(target), "class_name": "Contract", "methods": []}
        self.call("languages.coding.python.interface.create", args)
        ast.parse(target.read_text())
        self.call("languages.coding.python.interface.create", args, "ERROR")

    def test_interface_and_strategy(self):
        target = self.directory / "contract.py"
        self.call("languages.coding.python.interface.create", {"path": str(target), "class_name": "Contract", "methods": ["execute(a, b)", "validate()"]})
        self.assertIn("def execute(self, a, b):", target.read_text())
        self.assertIn("def validate(self):", target.read_text())
        args = {"path": str(target), "class_name": "Contract"}
        self.call("languages.coding.python.design_pattern.strategy", args)
        self.call("languages.coding.python.design_pattern.strategy", args, "ERROR")

    def test_imports_preserve_header_and_no_duplicates(self):
        self.source.write_text('#!/usr/bin/env python3\n"""docs"""\nfrom __future__ import annotations\nvalue = 1\n')
        args = {"path": str(self.source), "imports": ["import os", "import os"]}
        self.call("languages.coding.python.imports.add", args)
        self.call("languages.coding.python.imports.add", args)
        self.assertEqual(self.source.read_text().count("import os"), 1)
        self.assertEqual(ast.parse(self.source.read_text()).body[0].value.value, "docs")

    def test_indenter_retains_relative_nesting(self):
        result = self.call("files.text.indent", {"text": "    if yes:\n        run()\n", "op": "reset"})
        self.assertEqual(result["text"], "if yes:\n    run()\n")

    def test_python_body_preparation_retains_nested_logic(self):
        result = self.call("files.text.indent", {"text": "    if a < 0:\n        return 0\n    return a + b\n", "op": "python_body"})
        self.assertEqual(result["text"], "if a < 0:\n    return 0\nreturn a + b")
        self.assertEqual(result["validation"], "body_parse_only")
        self.assertIn("compiles the complete target", result["next"])

    def test_python_body_preparation_reports_mixed_indent_without_guessing(self):
        before = self.source.read_bytes()
        for spaces in (4, 8):
            bad = "check(value)\n" + " " * spaces + "return value"
            error = self.call("files.text.indent", {"text": bad, "op": "python_body"}, "ERROR")
            self.assertIn("line 2", error["message"])
            self.assertIn("column 0", error["message"])
            self.assertNotIn("text", error)
        self.assertEqual(self.source.read_bytes(), before)

    def test_python_body_preparation_rejects_empty_syntax_and_wrong_language(self):
        for args in ({"text": " \n", "op": "python_body"},
                     {"text": "return (", "op": "python_body"},
                     {"text": "return 1", "op": "python_body", "lang": "javascript"}):
            with self.subTest(args=args):
                self.call("files.text.indent", args, "ERROR")

    def test_method_tools_prepare_automatically_and_reject_before_write(self):
        for uri, field in (("languages.coding.python.method.fill", "logic"),
                           ("languages.coding.python.method.stage", "new_body")):
            args = {"path": str(self.source), "class_name": "Basic", "method_name": "execute", "commit": True}
            before = self.source.read_bytes()
            error = self.call(uri, dict(args, **{field: "value = a + b\n        return value"}), "ERROR")
            self.assertIn("line 2", error["message"])
            self.assertEqual(self.source.read_bytes(), before)
            self.assertEqual(list(self.directory.glob("*.atomic")), [])
            self.call(uri, dict(args, **{field: "        if a < 0:\n            return 0\n        return a + b"}))
            tree = ast.parse(self.source.read_text())
            body = tree.body[1].body[0].body
            self.assertIsInstance(body[0], ast.If)
            self.assertIsInstance(body[1], ast.Return)
            self.assertIn("class First:\n    def untouched(self):\n        pass", self.source.read_text())

    def test_body_parse_is_not_function_context_validation_or_execution(self):
        self.call("files.text.indent", {"text": "break", "op": "python_body"})
        before = self.source.read_bytes()
        self.call("languages.coding.python.method.fill", {"path": str(self.source),
                  "class_name": "Basic", "method_name": "execute", "logic": "break"}, "ERROR")
        self.assertEqual(self.source.read_bytes(), before)
        sentinel = self.directory / "must-not-exist.txt"
        self.call("files.text.indent", {"text": "open(" + repr(str(sentinel)) + ", 'w').write('executed')\nreturn 1", "op": "python_body"})
        self.assertFalse(sentinel.exists())

    def test_python_body_preserves_multiline_literal_contents(self):
        bodies = [
            'value = """hello\n  literal data\n"""\nreturn value',
            '        value = """hello\n  literal data\n"""\n        return value',
            '        value = """hello\n          literal data\n        """\n        return value',
        ]
        expected = ['hello\n  literal data\n', 'hello\n  literal data\n', 'hello\n          literal data\n        ']
        for body, value in zip(bodies, expected):
            with self.subTest(body=body):
                prepared = self.call('files.text.indent', {'text': body, 'op': 'python_body'})['text']
                self.assertEqual(ast.parse(prepared).body[0].value.value, value)
                self.call('languages.coding.python.method.fill', {'path': str(self.source),
                          'class_name': 'Basic', 'method_name': 'execute', 'logic': body})
                tree = ast.parse(self.source.read_text())
                self.assertEqual(tree.body[1].body[0].body[0].value.value, value)

    def test_method_body_preserves_multiline_fstring_and_continuation(self):
        for body in ('value = f"""hello {a}\n  literal {b}\n"""\nreturn value',
                     'value = "hello\\\n  literal data"\nreturn value'):
            with self.subTest(body=body):
                self.call('languages.coding.python.method.fill', {'path': str(self.source),
                          'class_name': 'Basic', 'method_name': 'execute', 'logic': body})
                actual = ast.parse(self.source.read_text()).body[1].body[0].body
                self.assertEqual([ast.dump(n) for n in actual], [ast.dump(n) for n in ast.parse(body).body])

    def test_method_macro_keeps_target_indent_and_replaces_decorated_old_body(self):
        for prefix in ('\t', '  '):
            source = ('def decorate(fn):\n    return fn\nclass Basic:\n' + prefix + 'def execute(self, a, b):\n'
                      + prefix * 2 + '@decorate\n' + prefix * 2 + 'def old_helper():\n'
                      + prefix * 3 + 'return a\n' + prefix * 2 + 'return old_helper()\n')
            self.source.write_text(source)
            self.call('languages.coding.python.method.fill', {'path': str(self.source),
                      'class_name': 'Basic', 'method_name': 'execute', 'logic': 'return a + b'})
            self.assertIn(prefix * 2 + 'return a + b', self.source.read_text())
            self.assertNotIn('@decorate', self.source.read_text())
            compile(self.source.read_text(), str(self.source), 'exec')

    def test_method_macro_refuses_a_transform_that_changes_literal_values(self):
        before = self.source.read_bytes()
        with patch('hornet.registry.assets._python_ops._literal_continuations', return_value=set()):
            error = self.call('languages.coding.python.method.fill', {'path': str(self.source),
                              'class_name': 'Basic', 'method_name': 'execute',
                              'logic': 'value = """hello\nworld\n"""\nreturn value'}, 'ERROR')
        self.assertIn('literal values', error['message'])
        self.assertEqual(self.source.read_bytes(), before)

    def test_bounded_read_and_preview(self):
        target = self.directory / "large.txt"
        target.write_text("a" * 70000 + "\n")
        self.call("files.text.lines", {"path": str(target), "action": "read_lines"}, "ERROR")
        result = self.call("files.text.atomic_edit", {"path": str(target), "action": "insert", "line_number": 1, "new_text": "hello", "commit": False}, "STAGED")
        self.assertTrue(result["content_omitted"])
        self.assertTrue(Path(result["shadow_path"]).exists())
        self.assertNotIn("shadow_content", result)

    def test_text_edit_ambiguous_and_one_based(self):
        target = self.directory / "plain.txt"
        target.write_text("alpha\nalpha\n")
        self.call("files.text.atomic_edit", {"path": str(target), "action": "replace", "target_text": "alpha", "new_text": "beta"}, "ERROR")
        self.call("files.text.atomic_edit", {"path": str(target), "action": "insert", "line_number": 1, "new_text": "first"})
        self.assertTrue(target.read_text().startswith("first\n"))

    def test_text_insert_adds_function_without_replacing_similar_name(self):
        source = 'def quote(value):\n    return value * 2\n'
        self.source.write_text(source, encoding='utf-8')
        self.call('files.text.atomic_edit', {'path': str(self.source), 'action': 'insert',
            'line_number': 3, 'new_text': '\ndef quotes(values):\n    return [quote(v) for v in values]'})
        updated = self.source.read_text(encoding='utf-8')
        self.assertTrue(updated.startswith(source))
        scope = {}
        exec(compile(updated, str(self.source), 'exec'), scope)
        self.assertEqual(scope['quote'](3), 6)
        self.assertEqual(scope['quotes']([0, 3, 5]), [0, 6, 10])
        self.assertEqual(scope['quotes']([]), [])

    def test_shell_exit_and_probe_error(self):
        self.call("runtime.shell.execute", {"command": "exit 7"}, "ERROR")
        self.call("runtime.python.probe", {"expression": "1 / 0"}, "ERROR")
        self.assertEqual(self.call("runtime.python.probe", {"expression": "2 + 3"})["stdout"].strip(), "5")

    def test_recipes_not_executable(self):
        self.call("recipe.atomic_commit", {}, "ERROR")

    def test_protocol_errors(self):
        server = RegistryMCPServer(self.registry)
        self.assertEqual(server.handle_request("{")["error"]["code"], -32700)
        self.assertEqual(server.handle_request("[]")["error"]["code"], -32600)
        response = server.handle_request(json.dumps({"jsonrpc":"2.0", "id":0,"method":"tools/call", "params":{"name":"call_proxy_tool", "arguments":{"uri":"data.json.query", "arguments":{}}}}))
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["id"], 0)

    def test_gateway_json_object_string(self):
        server = RegistryMCPServer(self.registry)
        def request(value):
            return server.handle_request(json.dumps({"jsonrpc":"2.0", "id":1, "method":"tools/call",
                "params":{"name":"call_proxy_tool", "arguments":{"uri":"data.json.query", "arguments":value}}}))["result"]
        result = request(json.dumps({"file_path":str(self.manifest), "target_path":"version"}))
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["proxy_response"]["value"], "1")
        for value in ('[]', 'null', '{bad', '{"file_path":"one","file_path":"two"}', '"double encoded"'):
            self.assertTrue(request(value)["isError"], value)

    def test_stdio_from_unrelated_directory(self):
        messages = [
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"ping"},
            {"jsonrpc":"2.0","id":3,"method":"tools/list"},
            {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_asset_details","arguments":{"uri":"mcp.json_query"}}},
            {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"call_proxy_tool","arguments":{"uri":"data.json.query","arguments":{"file_path":str(self.manifest),"target_path":"version"}}}}]
        result = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, " + repr(str(HERE)) + "); from hornet.registry.registry_mcp_server import main; main()"], input="\n".join(json.dumps(m) for m in messages)+"\n", capture_output=True, text=True, cwd=self.directory, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1,2,3,4,5])
        self.assertEqual(json.loads(replies[-1]["result"]["content"][0]["text"])["proxy_response"]["value"], "1")

    def test_relocated_root_and_index(self):
        root = self.directory / "relocated"
        for entry in self.registry.assets.values():
            target = root / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry["resolved_path"], target)
        registry = RegistryManager(root=root, db_path=self.directory / "index.db")
        self.assertEqual(registry.doctor()["issues"], [])
        registry.sync_index()
        registry.sync_index()
        self.assertTrue((self.directory / "index.db.pre-v2.bak").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

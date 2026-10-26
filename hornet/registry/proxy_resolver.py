import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from .registry_manager import RegistryManager
from .schema_validation import validate


def decode_arguments(value):
    """Accept exactly one JSON object or one JSON encoding of it; never eval/coerce."""
    if isinstance(value, str):
        def unique_keys(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"Duplicate JSON argument key: {key}")
                result[key] = item
            return result
        try:
            value = json.loads(value, object_pairs_hook=unique_keys)
        except ValueError as exc:
            raise ValueError(f"arguments must be an object or one valid JSON-encoded object: {exc}")
    if not isinstance(value, dict):
        raise ValueError("arguments must decode to an object, not an array, scalar, or another string")
    return value


class ProxyResolver:
    def __init__(self, db_path=None, registry=None):
        self.registry = registry or RegistryManager(db_path)

    def execute(self, uri, arguments=None):
        executed = False
        asset = None
        try:
            asset = self.registry.details(uri)
            if asset["type"] != "TOOL":
                raise ValueError("Recipe assets are read with read_recipe, not executed")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            validate(arguments, asset["inputSchema"])
            guarded = {"data.json.edit", "data.json.batch", "data.json.move", "data.json.ordered_edit",
                       "files.text.atomic_edit", "languages.coding.python.method.fill",
                       "languages.coding.python.design_pattern.strategy", "languages.coding.python.imports.add",
                       "languages.coding.python.method.stage", "languages.coding.python.design_pattern.stage"}
            uri_name = asset["uri"]
            default_commit = not uri_name.endswith(".stage")
            editing_lines = uri_name == "files.text.lines" and arguments.get("action") not in ("read_lines", "create_file")
            replacing_interface = uri_name in ("languages.coding.python.interface.create", "languages.coding.python.interface.stage") and Path(arguments["path"]).exists()
            if (uri_name in guarded or editing_lines or replacing_interface) and arguments.get("commit", default_commit):
                if not arguments.get("expected_sha256"):
                    raise ValueError("Read the target first using data.json.query or files.text.read; pass its source_sha256 as expected_sha256 to commit. Or request commit=false to preview.")
            path = Path(asset["resolved_path"])
            # Native asset output must never corrupt the MCP stdout stream.
            with contextlib.redirect_stdout(sys.stderr):
                spec = importlib.util.spec_from_file_location("hornet.registry.assets." + path.stem, path)
                if spec is None or spec.loader is None:
                    raise ValueError("Asset is not a loadable Python file")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                executed = True
                if asset["uri"] == "registry.advise":
                    result = {"status": "SUCCESS", "recommendations": self.registry.find(arguments["goal"], 5),
                              "next": "Read get_asset_details for a selected URI; keyword ranking is not a plan."}
                else:
                    result = module.execute(arguments)
            if not isinstance(result, dict) or result.get("status") not in ("SUCCESS", "STAGED", "ERROR", "EXCEPTION"):
                raise ValueError("Asset violated result contract: expected an object with explicit status")
            encoded = json.dumps(result, allow_nan=False)
            if len(encoded.encode("utf-8")) > 65536:
                if result.get("status") == "STAGED":
                    # Preserve evidence pointers without injecting a large preview into context.
                    result.pop("shadow_content", None)
                    result["content_omitted"] = True
                    result["message"] = "Preview exceeds 64 KiB; inspect shadow_path using bounded read_lines ranges."
                else:
                    raise ValueError("Tool result exceeds 64 KiB; request a smaller line range or a narrower JSON target_path")
            ok = result["status"] in ("SUCCESS", "STAGED")
            return {"status": result["status"] if ok else "ERROR", "was_proxied": True,
                    "contacted_mcp": False, "execution_kind": "local_python",
                    "asset_info": {"uri": asset["uri"], "requested_uri": uri, "version": asset["version"]},
                    "proxy_response": result}
        except Exception as exc:
            return {"status": "ERROR", "was_proxied": executed, "contacted_mcp": False,
                    "proxy_response": {"status": "ERROR", "message": str(exc)},
                    "next": "Read get_asset_details for the exact URI and inputSchema; do not guess argument names."}

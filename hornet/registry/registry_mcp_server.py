"""Synchronous MCP stdio server. stdout is JSON-RPC only; no network transport."""
import argparse
import json
from pathlib import Path
import sys
from .registry_manager import RegistryManager
from .proxy_resolver import ProxyResolver, decode_arguments
from .schema_validation import validate

GUIDE = """Use find_asset(query) to discover canonical registry URIs, then get_asset_details(uri)
to obtain the exact inputSchema, examples, effects and result rules. Call TOOL assets with
call_proxy_tool(uri, arguments); read RECIPE assets with read_recipe(uri).
A URI is an identifier, not a path, MCP server name, Python module or inferred function.
Never invent names or arguments. Paths refer to this server host; use the assigned goal
workspace, not a shared global contracts folder. Inspect isError and status; STAGED means
preview only, never completed work. Verify persisted results by reading them back.
To commit an existing-file edit, first read with data.json.query or
files.text.read; expected_sha256 from that read is required.
Keep relevant URIs and file/hash pointers in the cell return manifest; do not load the whole
catalog or old conversations. No tool success establishes application correctness.
Shell and Python probe run with the server user's privileges and are not a sandbox."""

def object_schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}

STRING = {"type": "string", "minLength": 1}
TOOLS = [
    {"name": "find_asset", "description": "Discover canonical tools/recipes by purpose or URI. OR keyword search; bounded results.",
     "inputSchema": object_schema({"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["query"])},
    {"name": "get_asset_details", "description": "Get exact arguments, examples, aliases and effects before calling an asset.",
     "inputSchema": object_schema({"uri": STRING}, ["uri"])},
    {"name": "read_recipe", "description": "Read one registered recipe as reference material; does not execute steps.",
     "inputSchema": object_schema({"uri": STRING}, ["uri"])},
    {"name": "call_proxy_tool", "description": "Execute a discovered TOOL URI using its inputSchema. Inspect isError/status.",
     "inputSchema": object_schema({"uri": STRING, "arguments": {"anyOf": [{"type": "object"}, {"type": "string"}],
         "description": "Prefer a JSON object. A single JSON-encoded object string is also accepted; the decoded object is validated against the asset schema."}}, ["uri", "arguments"])},
    {"name": "registry_status", "description": "Check catalog, schemas, paths and source syntax without executing assets.",
     "inputSchema": object_schema({})}
]

class RegistryMCPServer:
    def __init__(self, registry=None):
        self.registry = registry or RegistryManager()
        self.proxy = ProxyResolver(registry=self.registry)

    def error(self, req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def handle_request(self, request_json):
        try:
            data = json.loads(request_json)
        except (ValueError, TypeError):
            return self.error(None, -32700, "Parse error")
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or not isinstance(data.get("method"), str):
            return self.error(None, -32600, "Invalid request")
        if "id" not in data:
            return None
        req_id = data["id"]
        if type(req_id) not in (int, str):
            return self.error(None, -32600, "Invalid request id")
        params = data.get("params", {})
        if not isinstance(params, dict):
            return self.error(req_id, -32602, "params must be an object")
        method = data["method"]
        if method == "initialize":
            supported = ("2024-11-05", "2025-03-26", "2025-06-18")
            requested = params.get("protocolVersion")
            result = {"protocolVersion": requested if requested in supported else supported[-1],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "hornet-registry", "version": "2.0.0"}, "instructions": GUIDE}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            if params.get("cursor"):
                return self.error(req_id, -32602, "This server does not paginate tools")
            result = {"tools": TOOLS}
        elif method == "tools/call":
            tool = next((t for t in TOOLS if t["name"] == params.get("name")), None)
            if tool is None:
                return self.error(req_id, -32602, "Unknown tool name")
            try:
                args = params.get("arguments", {})
                validate(args, tool["inputSchema"])
                if tool["name"] == "find_asset":
                    payload = {"status": "SUCCESS", "assets": self.registry.find(args["query"], args.get("limit", 10)),
                               "next": "get_asset_details(uri)"}
                elif tool["name"] == "get_asset_details":
                    payload = {"status": "SUCCESS", "asset": self.registry.details(args["uri"])}
                elif tool["name"] == "read_recipe":
                    asset = self.registry.details(args["uri"])
                    if asset["type"] != "RECIPE":
                        raise ValueError("Asset is not a RECIPE")
                    payload = {"status": "SUCCESS", "uri": asset["uri"],
                               "content": Path(asset["resolved_path"]).read_text(encoding="utf-8")}
                elif tool["name"] == "registry_status":
                    payload = self.registry.doctor()
                else:
                    payload = self.proxy.execute(args["uri"], decode_arguments(args["arguments"]))
                    if isinstance(args["arguments"], str):
                        payload["arguments_format"] = "decoded_json_object"
            except Exception as exc:
                payload = {"status": "ERROR", "message": str(exc)}
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, allow_nan=False)}],
                      "isError": payload.get("status") not in ("SUCCESS", "STAGED")}
        else:
            return self.error(req_id, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def run(self):
        for line in sys.stdin:
            response = self.handle_request(line)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
                sys.stdout.flush()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", help="Asset root; defaults to the installed registry package")
    parser.add_argument("--allow-uri", action="append", help="Expose only these canonical assets (repeatable; aliases also resolve)")
    args = parser.parse_args()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    RegistryMCPServer(RegistryManager(root=args.root, allowed_uris=args.allow_uri)).run()

"""Synchronous MCP stdio server. stdout is JSON-RPC only; no network transport."""
import argparse
import json
import os
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

# The legacy gateway is standalone only. Coding cells import TOOLS and apply a
# separate scope, inspection and journal policy that this adapter must not bypass.
LEGACY_MATH_NAMES = frozenset((
    "matrix_addition", "matrix_multiplication", "matrix_transpose", "scalar_multiply",
    "matrix_determinant", "matrix_inverse", "vector_dot_product", "vector_cross_product",
    "vector_norm", "orthogonal_projection", "solve_linear_system", "find_null_space_basis",
    "find_column_space_basis", "compute_rank", "gram_schmidt_orthogonalization",
    "qr_decomposition", "lu_decomposition", "compute_eigenvalues",
    "singular_value_decomposition", "least_squares_solution", "matrix_rank", "parse_matrix",
    "process_matrix",
))
STANDALONE_TOOLS = TOOLS + [{
    "name": "call_registry_tool",
    "description": "Compatibility gateway: capability_name selects a canonical Hornet URI or a supported legacy math, Python, terminal, file-read, or web-search capability.",
    "inputSchema": object_schema({"tool_name": STRING, "arguments": {
        "type": "object", "properties": {"capability_name": {
            "type": "string", "minLength": 1,
            "description": "Canonical URI when tool_name is hornet, or the legacy operation/capability name."}},
        "required": ["capability_name"], "additionalProperties": True}},
                                 ["tool_name", "arguments"]),
}]


def legacy_registry_call(proxy, name, arguments):
    """Adapt compatibility calls to the same allowlisted, schema-validated resolver."""
    def wrap(status, payload):
        return {"container": "hornet_proxy_v1", "status": status, "tool": name,
                "payload": payload, "metadata": {}}

    args = dict(arguments)
    capability = args.pop("capability_name", None)
    if not isinstance(capability, str) or not capability:
        return wrap("error", "A nonempty capability_name is required.")
    if name == "user_input" and capability == "ask":
        if set(args) - {"question", "timeout"}:
            return wrap("error", "User input accepts question and timeout.")
        question, timeout = args.get("question"), args.get("timeout", 3600)
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            return wrap("error", "A non-empty question of at most 2000 characters is required.")
        if type(timeout) not in (int, float) or not 0 < timeout <= 86400:
            return wrap("error", "Timeout must be between 0 and 86400 seconds.")
        return wrap("success", {"status": "input_required", "question": question, "timeout": timeout,
                                "message": "Ask the user in the client conversation; this tool does not open a modal or wait."})
    if name == "hornet":
        try:
            asset = proxy.registry.details(capability)
            if asset["uri"] != capability:
                return wrap("error", "Use a canonical Hornet URI as capability_name.")
        except ValueError:
            return wrap("error", "Unknown or unavailable Hornet capability.")
        uri = capability
    elif name == "web_search" and capability == "search":
        if set(args) - {"query"}:
            return wrap("error", "Legacy web search accepts only query.")
        uri = "research.web.search"
    elif name == "file_manager" and capability == "read_file":
        if set(args) - {"path", "encoding"}:
            return wrap("error", "Legacy file read accepts only path and UTF-8 encoding.")
        encoding = args.pop("encoding", "utf-8")
        if not isinstance(encoding, str) or encoding.lower().replace("_", "-") not in ("utf-8", "utf8"):
            return wrap("error", "Only UTF-8 file reads are supported.")
        uri = "files.text.read"
    elif name == "terminal" and capability == "execute":
        if set(args) - {"command", "cwd", "shell"}:
            return wrap("error", "Legacy terminal accepts command, cwd and shell.")
        args.setdefault("shell", "powershell" if os.name == "nt" else "bash")
        args["timeout"] = 300
        uri = "runtime.shell.execute"
    elif name == "python_suite" and capability == "execute":
        if set(args) != {"code"}:
            return wrap("error", "Python execution requires exactly a code argument.")
        uri = "runtime.python.probe"
    elif name not in LEGACY_MATH_NAMES:
        return wrap("error", "Unsupported legacy tool or capability.")
    elif capability not in LEGACY_MATH_NAMES:
        return wrap("error", "capability_name must name a supported math operation.")
    else:
        if capability == "process_matrix":
            if "action" in args:
                return wrap("error", "Use the legacy operation argument, not action, for process_matrix.")
            if "operation" in args:
                args["action"] = args.pop("operation")
        elif "operation" in args:
            return wrap("error", "operation is not a legacy argument for this capability.")
        args["operation"] = capability
        uri = "math.linear_algebra.compute"
    result = proxy.execute(uri, args)
    payload = dict(result.get("proxy_response", {}))
    status = {"SUCCESS": "success", "STAGED": "staged"}.get(result.get("status"), "error")
    # Preserve the old wrapper and nested lowercase status without losing errors.
    payload["status"] = status
    return wrap(status, payload)

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
            result = {"tools": STANDALONE_TOOLS}
        elif method == "tools/call":
            tool = next((t for t in STANDALONE_TOOLS if t["name"] == params.get("name")), None)
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
                elif tool["name"] == "call_registry_tool":
                    payload = legacy_registry_call(self.proxy, args["tool_name"], args["arguments"])
                else:
                    payload = self.proxy.execute(args["uri"], decode_arguments(args["arguments"]))
                    if isinstance(args["arguments"], str):
                        payload["arguments_format"] = "decoded_json_object"
            except Exception as exc:
                payload = {"status": "ERROR", "message": str(exc)}
            result = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, allow_nan=False)}],
                      "isError": payload.get("status") not in ("SUCCESS", "STAGED", "success", "staged")}
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

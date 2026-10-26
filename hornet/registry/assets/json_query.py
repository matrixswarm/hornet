import json
from pathlib import Path
from ._common import execute_guarded, digest
from ._json_ops import get

@execute_guarded
def execute(args):
    raw = Path(args["file_path"]).read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    value = get(data, args["target_path"]) if args.get("target_path") else data
    return {"status": "SUCCESS", "value": value, "path": args["file_path"], "source_sha256": digest(raw)}

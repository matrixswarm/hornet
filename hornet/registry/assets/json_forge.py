import json
from ._common import execute_guarded

@execute_guarded
def execute(args):
    packet = {args.get("wrap_key", "content"): args["raw_content"]}
    return {"status": "SUCCESS", "value": packet, "json": json.dumps(packet, ensure_ascii=False)}

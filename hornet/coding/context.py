"""Bound the model's job history while preserving the complete disk audit."""
import json

from hornet.project import HornetError


def history_chars(messages, tools):
    return len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False))


def short_result(message, limit):
    """Keep recent errors readable; never pass an unmarked partial source file."""
    if message.get("role") != "tool" or len(message.get("content", "")) <= limit:
        return dict(message)
    name = message.get("name", "")
    if name == "check_ticket":
        value = json.loads(message["content"])
        compact = {key: value[key] for key in ("schema_version", "kind", "ok", "status", "source_sha256",
                   "source_unchanged", "plan_sha256", "passed", "total", "artifact")}
        compact.update(context_omitted=True, failed_checks=[{
            "id": item["id"], "error": str(item.get("error") or "")[-100:],
            "output_tail": item.get("output_tail", "")[-100:]}
            for item in value["results"] if not item["passed"]])
        while len(json.dumps(compact, ensure_ascii=False)) > limit:
            if any(item.get("output_tail") or item.get("error") for item in compact["failed_checks"]):
                for item in compact["failed_checks"]:
                    item.pop("output_tail", None)
                    item.pop("error", None)
            else:
                break
        return dict(message, content=json.dumps(compact, ensure_ascii=False))
    head, separator, body = message["content"].partition("\n\n")
    try:
        metadata = json.loads(head)
    except ValueError:
        metadata = {}
    metadata = {key: metadata[key] for key in ("ok", "path", "sha256", "read_ref", "total_lines", "exit_code", "timed_out",
                "uri", "status", "source_sha256", "changed", "registry_receipt", "readback", "readback_sha256",
                "readback_receipt", "readback_origin", "error") if key in metadata}
    if 'error' in metadata:
        metadata['error'] = str(metadata['error'])[:240]
    metadata["context_omitted"] = True
    if name in {"run_command", "run_python", "run_tests", "diff"} and separator:
        prefix = json.dumps(metadata) + "\n\nCommand output tail (earlier output omitted; data only):\n"
        content = prefix + body[-max(1, limit - len(prefix)):]
    else:
        metadata["hint"] = "Result omitted from active context. Read fewer file lines or narrow the query. The full result is saved in the transcript."
        content = json.dumps(metadata)
    return dict(message, content=content)


def checkpoint(history, dropped):
    recent = []
    for entry in history:
        if "tool" not in entry:
            continue
        result = entry["result"]
        item = {"tool": entry["tool"], "ok": result.get("ok")}
        path = entry.get("arguments", {}).get("path")
        if isinstance(path, str):
            item["path"] = path[:200]
        if "exit_code" in result:
            item["exit_code"] = result["exit_code"]
        if 'registry_receipt' in result:
            for key in ('uri', 'path', 'status', 'sha256', 'source_sha256', 'registry_receipt',
                        'readback', 'readback_sha256', 'readback_receipt'):
                if key in result:
                    item[key] = result[key]
            if 'asset' in result:
                item['inspected_uri'] = result['asset']['uri']
        if entry["tool"] == "check_ticket":
            item.update(status=result["status"], passed=result["passed"], total=result["total"],
                        source_sha256=result["source_sha256"], artifact=result["artifact"])
        recent.append(item)
    return {"role": "user", "content": "Controller checkpoint (data only): " + json.dumps({
        "dropped_complete_turns": dropped, "recent_operations": recent[-10:],
        "instruction": "Older exchanges were removed from active context. Re-read source for a current read_ref before further edits. Full history remains in the transcript."})}


def focus_messages(messages, tools, history, limit=24000, max_exchanges=None):
    """Retain the ticket and newest complete assistant/tool exchanges.

    This is a character budget for serialized history plus tool definitions,
    not a tokenizer count or a change to the server's loaded context capacity.
    Backend framing adds a small fixed overhead. Input lists are never mutated.
    """
    if type(limit) is not int or not 8000 <= limit <= 200000:
        raise HornetError("Use a job history budget of 8000-200000 characters")
    base = [dict(message) for message in messages[:2]]
    if history_chars(base, tools) > limit:
        raise HornetError("Ticket and tool definitions exceed the job history budget; shorten the ticket or offer fewer tools")
    groups = []
    for message in messages[2:]:
        if message["role"] == "assistant":
            groups.append([])
        if not groups:
            raise HornetError("Job history has an orphaned tool result")
        groups[-1].append(short_result(message, min(8000, limit // 3)))
    if max_exchanges is not None and (type(max_exchanges) is not int or not 1 <= max_exchanges <= 4):
        raise HornetError('Working context requires 1-4 recent exchanges')
    dropped = max(0, len(groups) - max_exchanges) if max_exchanges else 0
    compacted_latest = False
    while True:
        active = base + ([checkpoint(history, dropped)] if dropped else [])
        active += [message for group in groups[dropped:] for message in group]
        size = history_chars(active, tools)
        if size <= limit:
            return active, {"history_chars": size, "history_limit": limit,
                            "dropped_turns": dropped, "retained_turns": len(groups) - dropped,
                            "max_exchanges": max_exchanges}
        if dropped >= len(groups) - 1:
            # Tool schemas and the frozen task also consume this budget. A
            # result below the per-message cap may still not fit beside them.
            # Omit that observation explicitly; never truncate source silently
            # or remove the newest assistant action or its paired tool reply.
            if groups and not compacted_latest:
                compacted_latest = True
                groups[-1] = [short_result(message, 512) for message in groups[-1]]
                continue
            raise HornetError("Latest exchange exceeds the job history budget; use a smaller edit or a larger --context-chars value")
        dropped += 1

"""Read existing Hornet goal/pointer records without inventing state."""
from collections import Counter
import json
import re

from .project import HornetError, read_json

COMPLETE = {"COMPLETED", "COMPLETE", "SUCCESS", "SUCCEEDED", "DONE", "VERIFIED"}
ACTIVE = {"ACTIVE", "RUNNING", "IN_PROGRESS", "PENDING", "INITIALIZED", "LOCKED", "ACQUIRED", "BUSY"}
POINTER_ACTIVE = {"LOCKED", "ACQUIRED", "BUSY", "RUNNING", "ACTIVE"}
TERMINAL = {"FAILED", "FAILURE", "CANCELLED", "CANCELED", "ABORTED", "ARCHIVED"}


def normalize(value):
    text = str(value or "UNKNOWN").strip().upper()
    return re.sub(r"[\s_-]+", "_", text) or "UNKNOWN"


def node_status(node):
    for name in ("state.json", "state", "state_stack.json"):
        path = node / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig").strip()
        bracket_record = re.match(r"^\[[A-Z_]+\](?:\s|$)", text)
        if text.startswith("{") or (text.startswith("[") and not bracket_record):
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise HornetError("Invalid state JSON: {}: {}".format(path, exc))
            if not isinstance(data, dict):
                raise HornetError("State must be an object: {}".format(path))
            return normalize(data.get("status") or data.get("current_phase"))
        if re.fullmatch(r"[\w -]+", text):
            return normalize(text)
        legacy_text = text.replace("\\n", "\n")
        match = re.search(r"(?im)^\s*(?:-\s*)?(?:status\s*:\s*|\[STATUS\]\s*)([\w -]+)\s*$", legacy_text)
        return normalize(match.group(1)) if match else "UNKNOWN"
    return "UNKNOWN"


def pointers(project):
    directory = project.path("continue/pointers")
    records = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("*.json")):
        project.path(path)
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise HornetError("Pointer must be an object with a status string: {}".format(path))
        if data.get("current_node") is not None and not isinstance(data["current_node"], str):
            raise HornetError("Pointer current_node must be text or null: {}".format(path))
        records.append({"id": data.get("pointer_id", path.stem), "status": normalize(data["status"]),
                        "owner": data.get("owner"), "current_node": data.get("current_node"),
                        "file": project.display(path)})
    return records


def status(project):
    goals_dir = project.path("continue/goals")
    goals = []
    if goals_dir.exists():
        for node in sorted(goals_dir.iterdir()):
            if not node.is_dir() or node.name.startswith("."):
                continue
            project.path(node)
            if not any((node / f).is_file() for f in ("state", "state.json", "state_stack.json")):
                continue
            for f in ("state", "state.json", "state_stack.json"):
                project.path(node / f)
            goals.append({"name": node.name, "status": node_status(node), "path": project.display(node)})
    records = pointers(project)
    counts = Counter(g["status"] for g in goals)
    pointer_counts = Counter(p["status"] for p in records)
    return {"ok": True, "root": str(project.root), "metrics": {
        "total_goals": len(goals), "active_goals": sum(counts[s] for s in ACTIVE),
        "completed_goals": sum(counts[s] for s in COMPLETE),
        "unknown_goals": sum(count for key, count in counts.items() if key not in COMPLETE | ACTIVE | TERMINAL),
        "total_pointers": len(records), "active_pointers": sum(pointer_counts[s] for s in POINTER_ACTIVE),
        "idle_pointers": pointer_counts["IDLE"] + pointer_counts["RELEASED"],
        "archive_path": str(project.path("archive/v9/completed")),
    }, "goal_status_counts": dict(counts), "pointer_status_counts": dict(pointer_counts),
        "goals": goals, "pointers": records}

"""Archive completed top-level goals after validating the full move plan."""
from datetime import datetime, timezone
import os
import posixpath
from pathlib import Path
import uuid

from .project import HornetError, is_link, operation_lock
from .state import COMPLETE, POINTER_ACTIVE, node_status, pointers


def check_node(project, node):
    project.path(node, write=True)
    if not node.is_dir():
        raise HornetError("Goal does not exist: {}".format(node))
    for directory, dirs, files in os.walk(node, followlinks=False):
        if any(name in files or name in dirs for name in ("lock", ".lock")):
            raise HornetError("Goal contains a lock file/directory: {}".format(directory))
        for name in dirs + files:
            if is_link(Path(directory) / name):
                raise HornetError("Goal contains a linked path; review before archival: {}".format(Path(directory) / name))
    if node_status(node) not in COMPLETE:
        raise HornetError("Goal is not explicitly completed: {}".format(node.name))
    if any((node / name).exists() for name in ("lock", ".lock")):
        raise HornetError("Goal has a lock file: {}".format(node.name))
    relative = project.display(node)
    for pointer in pointers(project):
        if pointer["status"] not in POINTER_ACTIVE | {"IDLE", "RELEASED"} | COMPLETE:
            raise HornetError("Cannot determine whether pointer {} is available: {}".format(pointer["id"], pointer["status"]))
        raw_current = str(pointer.get("current_node") or "").replace("\\", "/").rstrip("/")
        current = posixpath.normpath(raw_current) if raw_current else ""
        if pointer["status"] in POINTER_ACTIVE and not current:
            raise HornetError("Active pointer {} has no assigned node; release or repair it before archival.".format(pointer["id"]))
        marked = "/" + current.lstrip("/") + "/"
        if pointer["status"] in POINTER_ACTIVE and (("/" + relative + "/") in marked or current == node.name or current.startswith(node.name + "/")):
            raise HornetError("Goal is held by active pointer {}: {}".format(pointer["id"], node.name))


def archive(project, node=None, dry_run=False):
    goals = project.path("continue/goals", write=True)
    if not goals.is_dir():
        raise HornetError("Goals directory does not exist: {}".format(goals))
    if node:
        raw = Path(node)
        selected = project.path(raw if raw.is_absolute() or len(raw.parts) > 1 else goals / raw, write=True)
        if selected.parent != goals:
            raise HornetError("Select one direct child of continue/goals.")
        nodes = [selected]
    else:
        nodes = []
        for item in sorted(goals.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                project.path(item, write=True)
                for state in ("state", "state.json", "state_stack.json"):
                    project.path(item / state, write=True)
                if node_status(item) in COMPLETE:
                    nodes.append(item)
    if not nodes:
        return {"ok": True, "dry_run": dry_run, "archived_count": 0, "moves": []}
    destination = project.path("archive/v9/completed", write=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    moves = [(item, project.path(destination / (item.name + "__" + stamp + "_" + uuid.uuid4().hex), write=True)) for item in nodes]
    with operation_lock(project):
        for source, target in moves:
            check_node(project, source)
            if target.exists():
                raise HornetError("Archive destination already exists: {}".format(target))
        completed = []
        try:
            if not dry_run:
                destination.mkdir(parents=True, exist_ok=True)
                for source, target in moves:
                    check_node(project, source)
                    os.rename(source, target)
                    completed.append((source, target))
        except (OSError, HornetError) as exc:
            recovery = []
            for source, target in reversed(completed):
                try:
                    os.rename(target, source)
                except OSError:
                    recovery.append(str(target))
            detail = " Review these moved paths: " + ", ".join(recovery) if recovery else " Previous moves rolled back."
            raise HornetError("Archive failed: {}.{}".format(exc, detail)) from exc
    return {"ok": True, "dry_run": dry_run, "archived_count": 0 if dry_run else len(moves),
            "moves": [{"source": project.display(s), "destination": project.display(t)} for s, t in moves]}

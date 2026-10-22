"""Short handles for confirmed reads; full file digests stay controller-owned."""
import hashlib
import json
from pathlib import PurePosixPath
import re

from hornet.project import HornetError


def path_key(value):
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ValueError("Invalid read-reference path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value or "\0" in value or not path.parts:
        raise ValueError("Invalid read-reference path")
    return path.as_posix()


def reference(job, call_id, path, digest):
    if (not isinstance(call_id, str) or not call_id or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("Invalid read-reference observation")
    # The handle is an identifier, not a secret or an authorization token.
    # Binding it to the job and worker prevents reuse in another cell/session.
    data = [job["job_id"], job["snapshot"]["binding"], call_id, path_key(path), digest]
    return "r_" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]


def observations(job):
    """Reconstruct references from the existing durable tool journal."""
    found = {}
    for entry in job["history"]:
        result = entry.get("result", {})
        if "read_ref" not in result:
            continue
        ref = result["read_ref"]
        if (entry.get("tool") != "read_file" or result.get("ok") is not True
                or result.get("path") != entry.get("arguments", {}).get("path")
                or ref != reference(job, entry.get("call_id"), result.get("path"), result.get("sha256"))
                or ref in found):
            raise ValueError("Read-reference journal is inconsistent")
        found[ref] = {"path": path_key(result["path"]), "sha256": result["sha256"]}
    return found


def issue(job, call, arguments, result, current):
    if result.get("ok") is not True:
        return result
    try:
        path = path_key(arguments.get("path"))
        ref = reference(job, call["id"], path, result.get("sha256"))
        if result.get("path") != arguments.get("path") or ref in observations(job):
            raise ValueError("Read observation does not match the request")
    except (ValueError, KeyError, TypeError) as exc:
        raise HornetError("Cannot confirm file read: " + str(exc)) from None
    if current["files"].get(path) != result["sha256"]:
        return {"ok": False, "path": arguments["path"],
                "error": "File changed while confirming the read. Read it again before editing."}
    return dict(result, read_ref=ref)


def resolve(job, arguments):
    """Translate one reference into its observed digest, never a newer digest."""
    if "read_ref" not in arguments:
        if "expected_sha256" not in arguments:
            raise ValueError("Supply read_ref from read_file, or expected_sha256 (empty only for a new file)")
        return arguments
    ref = arguments["read_ref"]
    if "expected_sha256" in arguments:
        raise ValueError("Supply read_ref OR expected_sha256, not both")
    if not isinstance(ref, str) or not ref:
        raise ValueError("Use the nonempty read_ref returned by read_file")
    observed = observations(job).get(ref)
    if observed is None:
        raise ValueError("Unknown read_ref for this job and worker. Read the file again.")
    if path_key(arguments.get("path")) != observed["path"]:
        raise ValueError("read_ref belongs to a different file. Read the target file first.")
    resolved = dict(arguments)
    del resolved["read_ref"]
    resolved["expected_sha256"] = observed["sha256"]
    return resolved

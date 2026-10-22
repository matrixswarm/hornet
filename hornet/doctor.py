"""Read-only local project checks, initially drafted by the Gemma worker."""
import sys

from .project import Project


def doctor(project: Project):
    checks = [{"name": "Python Version", "ok": sys.version_info[:2] >= (3, 9),
               "detail": "Python {}.{} (requires 3.9+)".format(*sys.version_info[:2])}]
    for directory in ("continue/goals", "continue/pointers", "recipes"):
        path = project.path(directory)
        ok = path.is_dir()
        detail = "Exists" if ok else ("Exists as a file, not a directory" if path.exists() else "Missing")
        checks.append({"name": "Directory: " + directory, "ok": ok, "detail": detail})
    return {"ok": all(item["ok"] for item in checks), "checks": checks}

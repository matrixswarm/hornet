"""ATS file contracts, evaluated relative to the selected project root."""
import hashlib
import re

from .project import HornetError, read_json


def verify(project, contract):
    contract_path = project.path(contract)
    data = read_json(contract_path)
    if not isinstance(data, dict) or not isinstance(data.get("target_files"), dict) or not data["target_files"]:
        raise HornetError("Contract needs a non-empty target_files object: {}".format(contract_path))
    planned = []
    allowed = {"exists", "contains", "not_contains", "min_size", "max_size", "sha256"}
    for name, rule in data["target_files"].items():
        if not isinstance(name, str) or not name or not isinstance(rule, dict):
            raise HornetError("Each target requires a path and a requirements object.")
        if set(rule) - allowed:
            raise HornetError("Unsupported requirements for {}: {}".format(name, sorted(set(rule) - allowed)))
        if not isinstance(rule.get("exists", True), bool):
            raise HornetError("exists must be a boolean: {}".format(name))
        for size in ("min_size", "max_size"):
            if size in rule and (type(rule[size]) is not int or rule[size] < 0):
                raise HornetError("{} must be a nonnegative integer: {}".format(size, name))
        if rule.get("min_size", 0) > rule.get("max_size", float("inf")):
            raise HornetError("min_size exceeds max_size: {}".format(name))
        if "sha256" in rule and (not isinstance(rule["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", rule["sha256"])):
            raise HornetError("sha256 must contain 64 hexadecimal characters: {}".format(name))
        compiled = {}
        for key in ("contains", "not_contains"):
            patterns = rule.get(key, [])
            if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
                raise HornetError("{} must be an array of regex strings: {}".format(key, name))
            try:
                compiled[key] = [re.compile(pattern) for pattern in patterns]
            except re.error as exc:
                raise HornetError("Invalid regex for {}: {}".format(name, exc))
        planned.append((name, project.path(name), rule, compiled))
    results = []
    for name, path, rule, compiled in planned:
        errors = []
        exists = path.exists()
        required = rule.get("exists", True)
        if exists != required:
            errors.append("File is missing" if required else "File should not exist")
        elif exists:
            if not path.is_file():
                errors.append("Target is not a regular file")
            else:
                raw = path.read_bytes()
                if len(raw) < rule.get("min_size", 0):
                    errors.append("File is smaller than min_size")
                if len(raw) > rule.get("max_size", float("inf")):
                    errors.append("File is larger than max_size")
                if "sha256" in rule and hashlib.sha256(raw).hexdigest() != rule["sha256"].lower():
                    errors.append("SHA-256 mismatch")
                if any(compiled.values()):
                    try:
                        text = raw.decode("utf-8-sig")
                    except UnicodeError:
                        errors.append("Regex requirements need UTF-8 text")
                    else:
                        errors.extend("Missing pattern: {}".format(p.pattern) for p in compiled["contains"] if not p.search(text))
                        errors.extend("Forbidden pattern: {}".format(p.pattern) for p in compiled["not_contains"] if p.search(text))
        results.append({"path": name, "passed": not errors, "failures": errors})
    return {"ok": all(r["passed"] for r in results), "contract": project.display(contract_path), "results": results}

"""Strict, dependency-free validation for the catalog's JSON Schema subset.

Supported: type, properties, required, additionalProperties, items, enum,
minimum, maximum, minLength, minItems, maxItems, anyOf, description, default.
No silent unknown keywords.
"""
import math

KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum",
        "minimum", "maximum", "minLength", "minItems", "maxItems", "description", "default", "anyOf"}
TYPES = {"object": dict, "array": list, "string": str, "integer": int,
         "number": (int, float), "boolean": bool, "null": type(None)}


def check_schema(schema):
    if not isinstance(schema, dict) or set(schema) - KEYS:
        raise ValueError("Unsupported schema keywords: %s" % (set(schema) - KEYS))
    if "type" in schema and schema["type"] not in TYPES:
        raise ValueError("Unsupported schema type")
    for child in schema.get("properties", {}).values():
        check_schema(child)
    if "items" in schema:
        check_schema(schema["items"])
    for option in schema.get("anyOf", []):
        check_schema(option)
    if isinstance(schema.get("additionalProperties"), dict):
        check_schema(schema["additionalProperties"])


def validate(value, schema, path="arguments"):
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                validate(value, option, path)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"{path}: does not match any permitted schema")
    kind = schema.get("type")
    if kind:
        ok = isinstance(value, TYPES[kind])
        if kind in ("integer", "number") and isinstance(value, bool):
            ok = False
        if not ok:
            raise ValueError(f"{path}: expected {kind}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path}: non-finite numbers are not JSON")
    if "enum" in schema and not any(type(value) is type(x) and value == x for x in schema["enum"]):
        raise ValueError(f"{path}: expected one of {schema['enum']}")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path}: missing {', '.join(sorted(missing))}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{path}.{key}")
            elif extra is False:
                raise ValueError(f"{path}: unknown argument {key!r}; read get_asset_details")
            elif isinstance(extra, dict):
                validate(item, extra, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path}: array length out of bounds")
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{i}]")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise ValueError(f"{path}: string too short")
    if type(value) in (int, float):
        if value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf")):
            raise ValueError(f"{path}: number out of bounds")

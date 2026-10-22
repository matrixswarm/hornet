"""JSON recipes are declarative data, never executable shell instructions."""
from importlib import resources
import json
from pathlib import Path
import re
from string import Formatter

from .project import HornetError, read_json

FIELDS = {"topic", "style", "length_words"}
ACTIONS = {"generate_article", "generate_initial_draft", "smash_patterns"}


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"].strip():
        raise HornetError("Recipe requires a nonempty name string.")
    params = data.get("parameters", {})
    if not isinstance(params, dict):
        raise HornetError("Recipe parameters must be an object.")
    words = params.get("length_words", 800)
    if type(words) is not int or not 1 <= words <= 20000:
        raise HornetError("Recipe length_words must be between 1 and 20000.")
    if not isinstance(params.get("style", "conversational"), str):
        raise HornetError("Recipe style must be text.")
    for key in ("prompt", "template"):
        value = data.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise HornetError("Recipe {} must be nonempty text.".format(key))
            try:
                for _, field, spec, conversion in Formatter().parse(value):
                    if field is not None and (field not in FIELDS or spec or conversion):
                        raise HornetError("Recipe placeholders must be simple topic, style, or length_words fields.")
            except ValueError as exc:
                raise HornetError("Invalid recipe template: {}".format(exc))
    if not data.get("prompt"):
        raise HornetError("Recipe requires a prompt string.")
    steps = data.get("steps", [{"action": "generate_article"}])
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise HornetError("Recipe steps must contain between 1 and 16 actions.")
    actions = [s.get("action") if isinstance(s, dict) else s for s in steps]
    if any(not isinstance(action, str) or action not in ACTIONS for action in actions):
        raise HornetError("Unsupported recipe action; supported: {}.".format(", ".join(sorted(ACTIONS))))
    if actions[0] not in {"generate_article", "generate_initial_draft"} or any(a != "smash_patterns" for a in actions[1:]):
        raise HornetError("Recipe must generate once, followed only by optional smash_patterns steps.")
    return data


def bundled():
    directory = resources.files("hornet").joinpath("data")
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in directory.iterdir() if path.name.endswith(".json")}


def load(project, name="article"):
    # Bare names search project recipes before the bundled defaults.
    if re.fullmatch(r"[\w-]+", name):
        for filename in (name + ".json", name + "_recipe.json"):
            local = project.path(Path("recipes") / filename)
            if local.is_file():
                return validate(read_json(local)), project.display(local)
        defaults = bundled()
        if name in defaults:
            return validate(defaults[name]), "bundled:" + name
        raise HornetError("Unknown recipe '{}'; use hornet recipe list.".format(name))
    path = project.path(name)
    return validate(read_json(path)), project.display(path)


def list_recipes(project):
    found = {name: {"name": name, "description": data.get("description", ""), "source": "bundled:" + name}
             for name, data in bundled().items()}
    directory = project.path("recipes")
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            project.path(path)
            data = validate(read_json(path))
            key = path.stem.removesuffix("_recipe")
            found[key] = {"name": key, "description": data.get("description", ""), "source": project.display(path)}
    return [found[key] for key in sorted(found)]

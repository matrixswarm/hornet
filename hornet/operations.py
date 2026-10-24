"""Command operations shared by the CLI and compatibility handlers."""
import json
import os
from pathlib import Path
import re

from . import __version__
from .project import HornetError, Project, is_link, read_json, write_text
from .recipes import bundled, load, list_recipes
from .generation import generate
from .contracts import verify
from .archival import archive
from .state import status
from .text import clean_text
from .doctor import doctor


def initialize(name, path):
    if not name.strip() or any(c in name for c in "\r\n\x00"):
        raise HornetError("Provide a nonempty, single-line project name.")
    raw = Path(path).expanduser().absolute()
    if is_link(raw) or any(is_link(p) for p in raw.parents):
        raise HornetError("Initialize a real directory rather than a linked path.")
    raw.mkdir(parents=True, exist_ok=True)
    project = Project(raw)
    marker = project.path(".hornet.json", write=True)
    if marker.exists():
        config = read_json(marker)
        if not isinstance(config, dict) or config.get("name") != name:
            raise HornetError("Existing project configuration has a different name or is invalid.")
    dirs = ("continue/pointers", "continue/goals", "bin", "recipes", "codex/capabilities", "archive/v9/completed", "protocols/fragments", "output")
    files = {".hornet.json": json.dumps({"schema_version": 1, "name": name, "cli_version": __version__}, indent=2) + "\n",
             "README.md": "# {}\n\nMatrixSwarm Hornet project.\n\nUse `hornet status`, `hornet recipe list`, and `hornet --help`.\n".format(name)}
    files.update({"recipes/" + n + ".json": json.dumps(d, indent=2) + "\n" for n, d in bundled().items()})
    # Validate all planned paths before writing scaffold contents.
    for directory in dirs:
        dest = project.path(directory, write=True)
        if dest.exists() and not dest.is_dir():
            raise HornetError("Scaffold directory is occupied by a file: {}".format(dest))
    for filename in files:
        dest = project.path(filename, write=True)
        if dest.exists() and not dest.is_file():
            raise HornetError("Scaffold file is occupied by a directory: {}".format(dest))
    created, preserved = [], []
    for directory in dirs:
        project.path(directory, write=True).mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        dest = project.path(filename, write=True)
        if dest.exists():
            preserved.append(filename)
        else:
            write_text(dest, content)
            created.append(filename)
    return {"ok": True, "root": str(project.root), "created": created, "preserved": preserved}


def execute(args):
    if args.command == "init":
        target = args.path or args.root or os.environ.get("HORNET_ROOT") or os.environ.get("HORNET_PATH") or "."
        return initialize(args.name, target)
    project = Project(args.root, require=args.command in {"status", "archive"})
    if args.command == 'hive':
        from . import hive
        if args.hive_action == 'init':
            return hive.initialize(project)
        if args.hive_action == 'list':
            return hive.listing(project)
        if args.hive_action == 'terminal':
            from .hive_terminal import run
            return run(project, args)
        if args.hive_action == 'supervise':
            from .coding.supervisor import command
            return command(project, args)
        if args.hive_action == 'resume-cell':
            from .coding import cell_actions
            return cell_actions.resume(project, args.run, args.cell)
        return hive.assign(project, args.run, args.cell, args.agent)
    if args.command == 'plan':
        from .coding import planning
        return planning.command(project, args)
    if args.command in {'questions', 'answer'}:
        from .coding import questions
        if args.command == 'questions':
            return questions.listing(project, args.run)
        text = (project.path(args.text_file).read_text(encoding='utf-8-sig') if args.text_file else
                args.cancel if args.cancel is not None else args.text)
        return questions.respond(project, args.run, args.question_id, text, cancel=args.cancel is not None)
    if args.command == "worker-status":
        from .coding.transport import SSHWorker
        return SSHWorker(project.path(args.worker_config)).preflight()
    if args.command == "work":
        from .coding.agent import work
        return work(project, args)
    if args.command in {'autowork', 'autoresume'}:
        from .coding import autonomous
        return (autonomous.start if args.command == 'autowork' else autonomous.resume)(project, args)
    if args.command in {'revise', 'goals', 'goal-files'}:
        from .coding import goals
        if args.command == 'revise':
            return goals.revise(project, args)
        return goals.listing(project, args.run_dir) if args.command == 'goals' else goals.inspect(project, args.run)
    if args.command == 'control':
        from .coding.control import send
        action = ('pause' if args.pause else 'continue' if args.continue_run else 'stop' if args.stop else 'message')
        return send(project, args.run, action, args.message)
    if args.command == "resume":
        from .coding.agent import resume
        return resume(project, args)
    if args.command == "doctor":
        return doctor(project)
    if args.command == "status":
        return status(project)
    if args.command == "verify":
        return verify(project, args.contract)
    if args.command == "archive":
        return archive(project, args.node, args.dry_run)
    if args.command == "recipe":
        if args.recipe_action == "list":
            return {"ok": True, "recipes": list_recipes(project)}
        data, origin = load(project, args.recipe_name)
        return {"ok": True, "recipe": data, "source": origin}
    if args.command == "smash":
        source = project.path(args.input)
        text = clean_text(source.read_text(encoding="utf-8-sig"))
        target = project.path(args.output or ("output/smashed_" + source.name), write=True)
        if target == source:
            raise HornetError("Choose a separate output file to preserve the input.")
        write_text(target, text, force=args.force)
        return {"ok": True, "output": str(target), "characters": len(text)}
    if args.command == "generate":
        recipe, origin = load(project, args.recipe)
        slug = re.sub(r"[^\w-]+", "_", args.topic, flags=re.UNICODE).strip("_")[:70] or "article"
        target = project.path(args.output or ("output/article_" + slug + ".md"), write=True)
        if target.exists() and not target.is_file():
            raise HornetError("Output path is not a regular file: {}".format(target))
        if target.exists() and not args.force:
            raise HornetError("Output already exists: {}; choose another path or --force.".format(target))
        result = generate(recipe, args.topic, args.style, args.words, args.provider, args.model, args.base_url, args.timeout, args.max_tokens)
        text = result.pop("text")
        write_text(target, text, force=args.force)
        return dict(result, ok=True, output=str(target), recipe=origin, characters=len(text))
    raise HornetError("Unsupported command.")

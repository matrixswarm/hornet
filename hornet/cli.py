"""Argument parsing, consistent output, and process exit status."""
import argparse
import json
import sys

from . import __version__
from .operations import execute
from .project import HornetError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise HornetError(message)


def common(parser):
    parser.add_argument("--root", "-C", default=argparse.SUPPRESS, help="Project root (default: discover from current directory)")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Machine-readable output")


def build_parser():
    parser = Parser(prog="hornet", description="MatrixSwarm Hornet - Phoenix Orchestrator CLI")
    parser.set_defaults(root=None, json=False)
    common(parser)
    parser.add_argument("--version", action="version", version="matrixswarm-hornet " + __version__)
    commands = parser.add_subparsers(dest="command")
    init = commands.add_parser("init", help="Create a project while preserving existing files")
    common(init)
    init.add_argument("--name", "-n", required=True)
    init.add_argument("--path", "-p", help="Directory to initialize (default: selected root/current directory)")
    stat = commands.add_parser("status", help="Read actual goal and pointer state")
    common(stat)
    stat.add_argument("--verbose", "-v", action="store_true")
    verify = commands.add_parser("verify", help="Evaluate an ATS file contract")
    common(verify)
    verify.add_argument("--contract", "-c", required=True, help="JSON contract path, relative to project root")
    archive = commands.add_parser("archive", help="Move explicitly completed, unlocked goals to cold storage")
    common(archive)
    selection = archive.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--node", "-n", help="Direct child of continue/goals (name or path)")
    archive.add_argument("--dry-run", action="store_true", help="Validate and preview without moving goals")
    recipe = commands.add_parser("recipe", help="List or inspect declarative recipes")
    common(recipe)
    actions = recipe.add_subparsers(dest="recipe_action", required=True)
    listing = actions.add_parser("list", help="List project and bundled recipes")
    common(listing)
    show = actions.add_parser("show", help="Validate and show a recipe")
    common(show)
    show.add_argument("recipe_name", help="Recipe name or JSON path")
    generate = commands.add_parser("generate", help="Generate a model draft or an explicit offline outline")
    common(generate)
    generate.add_argument("--recipe", "-r", default="article")
    generate.add_argument("--topic", "-t", required=True)
    generate.add_argument("--style", "-s")
    generate.add_argument("--words", type=int)
    generate.add_argument("--provider", choices=("compatible", "template"), default="compatible")
    generate.add_argument("--model", help="Loaded model ID (or HORNET_MODEL)")
    generate.add_argument("--base-url", help="Chat-completions base URL (or HORNET_BASE_URL)")
    generate.add_argument("--timeout", type=float, default=60)
    generate.add_argument("--max-tokens", type=int, default=2048)
    generate.add_argument("--output", "-o", help="Output file inside the project")
    generate.add_argument("--force", action="store_true", help="Replace an existing output file")
    smash = commands.add_parser("smash", help="Clean invisible separators and whitespace deterministically")
    common(smash)
    smash.add_argument("--input", "-i", required=True)
    smash.add_argument("--output", "-o")
    smash.add_argument("--force", action="store_true")
    worker = commands.add_parser("worker-status", help="Check the pinned localhost coding worker")
    common(worker)
    worker.add_argument("--worker-config", required=True)
    work = commands.add_parser("work", help="Run one coding ticket in the isolated Ubuntu worker; export changes for review")
    common(work)
    work.add_argument("--worker-config", required=True)
    ticket = work.add_mutually_exclusive_group(required=True)
    ticket.add_argument("--task")
    ticket.add_argument("--task-file")
    work.add_argument("--acceptance-file", help="Operator-authored JSON behavior checks, relative to project root")
    work.add_argument("--max-repairs", type=int, default=2, help="Automatic verification repair attempts (0-3; cumulative across resume)")
    work.add_argument("--model")
    work.add_argument("--base-url")
    work.add_argument("--protocol", choices=("native", "lmstudio-json"), default="native",
                      help="Native tool calls or LM Studio constrained JSON actions with thinking disabled")
    work.add_argument("--max-tokens", type=int, default=4096)
    work.add_argument("--timeout", type=int, default=180)
    work.add_argument("--max-turns", type=int, default=12)
    work.add_argument("--max-tools", type=int, default=40)
    work.add_argument("--context-chars", type=int, default=24000,
                      help="Active job history budget, including tool definitions (8000-200000 characters)")
    work.add_argument("--tools", nargs="+", choices=("list_files", "read_file", "edit_file", "search", "write_file", "run_command", "run_python", "run_tests", "check_ticket", "ask_user", "diff"),
                      help="Offer only these tools to the model (default: all coding tools)")
    work.add_argument("--run-dir", default="coding/runs")

    resume = commands.add_parser("resume", help="Verify and continue an interrupted coding job")
    common(resume)
    resume.add_argument("run", help="Existing coding run directory inside the selected project")
    resume.add_argument("--model", help="Override the model for the next attempt")
    resume.add_argument("--base-url", help="Override the model endpoint for the next attempt")
    resume.add_argument("--max-turns", type=int, help="Total job turn limit, including earlier attempts (maximum 30)")
    resume.add_argument("--max-tools", type=int, help="Total job tool limit, including earlier attempts (maximum 80)")
    resume.add_argument("--max-tokens", type=int, help="Output-token budget per model response")
    resume.add_argument("--timeout", type=int, help="Socket timeout per model request")
    resume.add_argument("--context-chars", type=int, help="Active job history budget")
    resume.add_argument("--protocol", choices=("native", "lmstudio-json"), help="Model protocol for the next attempt")

    for name, description in [('autowork', 'Work on a goal in its own VM directory'),
                              ('revise', 'Revise selected files in a new copy of a completed goal')]:
        auto = commands.add_parser(name, help=description)
        common(auto)
        if name == 'revise':
            auto.add_argument('run', help='Completed parent goal directory')
            auto.add_argument('--file', dest='files', action='append', required=True, help='Target relative file; repeat for up to four')
        else:
            auto.add_argument('--worker-config', required=True)
        goal = auto.add_mutually_exclusive_group(required=True)
        goal.add_argument('--goal')
        goal.add_argument('--goal-file')
        auto.add_argument('--model')
        auto.add_argument('--base-url')
        auto.add_argument('--protocol', choices=('native', 'lmstudio-json'), default='lmstudio-json')
        auto.add_argument('--max-tokens', type=int, default=3072)
        auto.add_argument('--timeout', type=int, default=120)
        auto.add_argument('--max-turns', type=int, default=30, help='Total model turns shared across all roles and cells')
        auto.add_argument('--max-tools', type=int, default=80, help='Total model tool operations across the recipe')
        auto.add_argument('--max-repairs', type=int, default=2)
        auto.add_argument('--context-chars', type=int, default=16000)
        auto.add_argument('--max-cells', type=int, default=3)
        auto.add_argument('--recon-tree', action='store_true', help='Read-only recon, depth-2 child work and bounded recent context')
        auto.add_argument('--research-cells', action='store_true', help='Allow cited read-only research branches feeding coding cells; implies recon-tree')
        auto.add_argument('--cell-turns', type=int, default=10)
        auto.add_argument('--cell-tools', type=int, default=20)
        auto.add_argument('--run-dir', default='coding/recipes')
    goals = commands.add_parser('goals', help='List saved coding goals without loading a model')
    common(goals)
    goals.add_argument('--run-dir', default='coding/recipes')
    files = commands.add_parser('goal-files', help='Show file hashes, cells and revision provenance')
    common(files)
    files.add_argument('run')
    auto_resume = commands.add_parser('autoresume', help='Continue a saved autonomous recipe with its frozen contracts')
    common(auto_resume)
    auto_resume.add_argument('run')
    control = commands.add_parser('control', help='Queue file-based operator steering for an autonomous recipe')
    common(control)
    control.add_argument('run')
    action = control.add_mutually_exclusive_group(required=True)
    action.add_argument('--pause', action='store_true')
    action.add_argument('--continue', dest='continue_run', action='store_true')
    action.add_argument('--stop', action='store_true')
    action.add_argument('--message')

    questions = commands.add_parser('questions', help='Show saved questions and answers; no model or worker connection')
    common(questions)
    questions.add_argument('run', help='Exact goal or coding run directory')
    answer = commands.add_parser('answer', help='Save an answer for one question; execution resumes only when requested')
    common(answer)
    answer.add_argument('run', help='Exact goal or coding run directory')
    answer.add_argument('question_id')
    reply = answer.add_mutually_exclusive_group(required=True)
    reply.add_argument('--text', help='Your answer; choices are suggestions and free text is accepted')
    reply.add_argument('--text-file', help='UTF-8 answer file inside the project')
    reply.add_argument('--cancel', help='Stop this question with a reason; the cell remains stopped')

    doctor = commands.add_parser("doctor", help="Run diagnostic checks on the project")
    common(doctor)
    return parser


def render(args, result):
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get('status') in {'waiting_for_user', 'question_cancelled'}:
        print('Waiting for you' if result['status'] == 'waiting_for_user' else 'Question cancelled; work remains stopped')
        print(result['question'])
        for number, choice in enumerate(result.get('choices', []), 1):
            print('  {}. {}'.format(number, choice))
        print('Goal: {} | Cell: {}'.format(result['goal_id'], result['cell_id']))
        print('Run: ' + result['run_dir'])
        print('Question: ' + result['question_id'])
        print(result['message'])
        if result['status'] == 'waiting_for_user':
            print('hornet answer "{}" {} --text "YOUR ANSWER"'.format(result['run_dir'], result['question_id']))
    elif args.command == 'questions':
        if not result['questions']:
            print('No saved questions for this run.')
        for item in result['questions']:
            print('{} [{}] cell={}'.format(item['question_id'], item['status'], item['cell_id']))
            print('  ' + item['question'])
            for choice in item['choices']:
                print('    ' + choice)
            if item['answer'] is not None:
                print('  Response: ' + item['answer'])
    elif args.command == 'answer':
        print('{}: {}'.format(result['status'], result['question_id']))
        print(result['next'])
    elif args.command == "status":
        print("Project: " + result["root"])
        for key, value in result["metrics"].items():
            print("{}: {}".format(key.replace("_", " ").capitalize(), value))
        if args.verbose:
            for goal in result["goals"]:
                print("Goal {}: {}".format(goal["name"], goal["status"]))
            for pointer in result["pointers"]:
                print("Pointer {}: {} owner={} node={}".format(pointer["id"], pointer["status"], pointer["owner"], pointer["current_node"]))
    elif args.command == "verify":
        print("VERIFICATION " + ("PASSED" if result["ok"] else "FAILED"))
        for item in result["results"]:
            print("{} {}".format("PASS" if item["passed"] else "FAIL", item["path"]))
            for failure in item["failures"]:
                print("  " + failure)
    elif args.command == "archive":
        print("{}: {} goal(s)".format("Preview" if result["dry_run"] else "Archived", len(result["moves"])))
        for move in result["moves"]:
            print("{} -> {}".format(move["source"], move["destination"]))
    elif args.command == "recipe":
        if args.recipe_action == "list":
            for recipe in result["recipes"]:
                print("{} [{}] - {}".format(recipe["name"], recipe["source"], recipe["description"]))
        else:
            print(json.dumps(result["recipe"], ensure_ascii=False, indent=2))
    elif args.command == "worker-status":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command in {"work", "resume"}:
        print("{}: {}".format(result["status"], result["run_dir"]))
        if "acceptance_status" in result:
            print("Acceptance: {} ({}/{})".format(result["acceptance_status"], result["acceptance_passed"], result["acceptance_total"]))
        if result.get("error"):
            print(result["error"])
        for path in result.get("changed_files", []):
            print("  " + path)
    elif args.command in {'autowork', 'autoresume', 'control', 'revise', 'goals', 'goal-files'}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "init":
        print("Project: {}\nCreated {} files; preserved {} existing files.".format(result["root"], len(result["created"]), len(result["preserved"])))
    elif args.command == "doctor":
        print("DIAGNOSTIC " + ("PASSED" if result["ok"] else "FAILED"))
        for item in result["checks"]:
            print("{} {}: {}".format("PASS" if item["ok"] else "FAIL", item["name"], item["detail"]))
    else:
        print("{}: {}".format(result.get("kind", "Written"), result["output"]))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return 0
        result = execute(args)
        render(args, result)
        return 0 if result.get("ok") else 1
    except (HornetError, OSError, UnicodeError, ValueError) as exc:
        if "--json" in argv:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print("Error: {}".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

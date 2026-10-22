"""Durable coding jobs, explicit action boundaries and verified continuation."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
import uuid
from urllib.parse import urlsplit

from hornet.project import HornetError, write_text
from hornet import hive
from .agent import DEFAULT_NAMES, NAMES, SYSTEM, TOOLS, change_state, tool_result_content, validate_call
from . import acceptance, ticket, read_refs, workspaces, operations, questions
from .context import focus_messages
from .locking import WorkerLock
from .model import completion, lmstudio_json
from .transport import SSHWorker

READ_ONLY = {"list_files", "read_file", "search", "diff"}
PHASES = {"model", "model_request", "dispatch", "tool_in_flight", "final_tests", "verification_in_flight", "complete", "waiting_for_user"}


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(worker):
    value = worker.call("snapshot")
    if (not isinstance(value, dict) or not value.get("ok") or not isinstance(value.get("binding"), dict) or not value["binding"]
            or not isinstance(value.get("files"), dict)
            or any(not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                   for path, digest in value["files"].items())):
        raise HornetError("Could not verify a complete worker snapshot: " + str(value.get("error", "invalid manifest") if isinstance(value, dict) else "invalid manifest"))
    return {"binding": value["binding"], "files": value["files"]}


def ready(worker):
    value = worker.call("status")
    if not value.get("ok") or not value.get("ready") or value.get("platform") != "linux" or value.get("uid") in {None, 0}:
        raise HornetError("Worker must be ready in the dedicated Linux VM under a non-root account")


def settings(args, project=None):
    value = {key: getattr(args, key) for key in ("worker_config", "max_tokens", "timeout", "max_turns", "max_tools")}
    value.update(model=args.model or os.environ.get("HORNET_MODEL"),
        base_url=args.base_url or os.environ.get("HORNET_BASE_URL", "http://127.0.0.1:1234/v1"),
        protocol=getattr(args, "protocol", "native"), context_chars=getattr(args, "context_chars", 24000),
        tools=sorted(getattr(args, "tools", None) or DEFAULT_NAMES), max_repairs=getattr(args, "max_repairs", 2))
    if getattr(args, 'workspace', None) is not None:
        value['workspace'] = workspaces.identifier(args.workspace)
    if project is not None:
        hive.configure(project, args, value)
    validate_settings(value)
    return value


def validate_settings(value):
    if value.get('hive') is not None:
        registry = hive.validate_binding(value['hive'])
        profile = registry['agents'][value['hive']['agent']]
        if any(value.get(key) != profile[key] for key in ('model', 'base_url', 'protocol')):
            raise HornetError('Saved model settings differ from the named hive agent')
    if value.get('workspace') is not None:
        workspaces.identifier(value['workspace'])
    limits = {"max_turns": (1, 30), "max_tools": (1, 80), "max_tokens": (1, 16384),
              "timeout": (1, 900), "context_chars": (8000, 200000), "max_repairs": (0, 3)}
    for key, (low, high) in limits.items():
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            raise HornetError("Use {} {}-{}".format(key.replace("_", "-"), low, high))
    if not isinstance(value.get("model"), str) or not value["model"]:
        raise HornetError("Select --model or HORNET_MODEL")
    if (value.get("protocol") not in {"native", "lmstudio-json"} or not isinstance(value.get("tools"), list)
            or not value["tools"] or not set(value["tools"]) <= NAMES):
        raise HornetError("Unknown coding protocol or tool selection")
    for key in ("worker_config", "base_url"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise HornetError("Invalid saved " + key)
    parsed = urlsplit(value["base_url"])
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise HornetError("Invalid model base URL; credentials must use HORNET_API_KEY")


def save(run, job):
    job["updated_at"] = now()
    write_text(run / "checkpoint.json", json.dumps(job, ensure_ascii=False, indent=2), force=True)
    # The atomic checkpoint is authoritative if interruption splits these writes.
    write_text(run / "transcript.json", json.dumps(job["history"], ensure_ascii=False, indent=2), force=True)


def load(project, run):
    path = project.path(run / "checkpoint.json", write=True)
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Checkpoint exceeds 32 MiB")
        job = json.loads(path.read_text(encoding="utf-8"))
        if (job["schema_version"] not in {1, 2} or job["project_root"] != str(project.root)
                or job["run_dir"] != str(run) or job["phase"] not in PHASES
                or not isinstance(job["task"], str) or not 1 <= len(job["task"]) <= 20000
                or not isinstance(job["history"], list) or not isinstance(job["messages"], list)
                or job["messages"][:2] != [{"role": "system", "content": job["messages"][0]["content"]},
                                           {"role": "user", "content": job["task"]}]
                or not isinstance(job["pending"], list) or len(job["pending"]) > 8
                or not isinstance(job["snapshot"]["files"], dict)
                or not isinstance(job["snapshot"]["binding"], dict)
                or not isinstance(job["initial_state"], dict)
                or not isinstance(job["seen_ids"], list)
                or type(job["turns_used"]) is not int or not 0 <= job["turns_used"] <= 30
                or type(job["tools_used"]) is not int or not 0 <= job["tools_used"] <= 80):
            raise ValueError("Checkpoint schema mismatch")
        if job["schema_version"] == 1:
            job["settings"]["max_repairs"] = 0
            job.update(schema_version=2, acceptance=None, acceptance_sha256=None,
                       verification_rounds=0, repairs_used=0)
        else:
            if job["acceptance"] is not None:
                acceptance.validate(job["acceptance"])
                if (acceptance.digest(job["acceptance"]) != job["acceptance_sha256"]
                        or (run / "acceptance.json").read_text(encoding="utf-8") != acceptance.encoded(job["acceptance"])):
                    raise ValueError("Saved acceptance plan changed")
            elif job["acceptance_sha256"] is not None:
                raise ValueError("Missing acceptance plan")
            for key in ("verification_rounds", "repairs_used"):
                if type(job[key]) is not int or not 0 <= job[key] <= 100:
                    raise ValueError("Invalid verification counters")
        validate_settings(job["settings"])
        if job.get('context_window') is not None and (type(job['context_window']) is not int or job['context_window'] != 2):
            raise ValueError('Invalid fixed working context window')
        for call in job["pending"]:
            name, _ = validate_call(call)
            if name not in job["settings"]["tools"]:
                raise ValueError("Pending operation was not offered")
        if job["phase"] in {"dispatch", "tool_in_flight"} and not job["pending"]:
            raise ValueError("Missing pending operation")
        if job["phase"] not in {"dispatch", "tool_in_flight", "waiting_for_user"} and job["pending"]:
            raise ValueError("Unexpected pending operations")
        if job.get('waiting_question'):
            questions.validate_owner(job['waiting_question'], job, run)
        elif job['phase'] == 'waiting_for_user':
            raise ValueError('Missing waiting question')
        for item in job.get('answered_questions', []):
            if questions.resolution(item['reference']) != item['answer']:
                raise ValueError('Saved question answer changed')
        read_refs.observations(job)
        active = job.get('in_flight_operation')
        if active is not None:
            tool, arguments = validate_call(job['pending'][0])
            if tool in {'write_file', 'edit_file'}:
                arguments = read_refs.resolve(job, arguments)
            operations.validate_checkpoint(job, tool, arguments)
        elif job.get('operation_protocol') not in (None, 1):
            raise ValueError('Unknown operation receipt protocol')
        operations.validate_history(run, job)
        return job
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        raise HornetError("No valid resumable checkpoint at {}; jobs created before 0.4.0 cannot be resumed".format(run)) from None


def work(project, args, worker=None, request_model=None, boundary=None):
    config = settings(args, project)
    plan = acceptance.read(project, getattr(args, "acceptance_file", None))
    task = args.task or project.path(args.task_file).read_text(encoding="utf-8-sig")
    if not task.strip() or len(task) > 20000:
        raise HornetError("Coding ticket must contain 1-20000 characters")
    worker = worker or SSHWorker(project.path(config["worker_config"]), config.get('workspace'))
    lock_path = workspaces.lock_path(project, config, worker)
    with WorkerLock(lock_path) as lock, hive.worker_claim(config, worker) as shared_lock:
        ready(worker)
        initial = worker.call("changes")
        if not initial.get("ok"):
            raise HornetError("Could not inspect the worker's initial change set")
        current = snapshot(worker)
        run = project.path(Path(args.run_dir) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"), write=True)
        run.mkdir(parents=True)
        lock.set_run(run)
        if shared_lock is not None:
            shared_lock.set_run(run)
        job = {"schema_version": 2, "job_id": uuid.uuid4().hex, "project_root": str(project.root),
            "run_dir": str(run), "task": task, "settings": config, "snapshot": current,
            "initial_state": change_state(initial.get("files", [])), "phase": "model", "pending": [],
            "messages": [{"role": "system", "content": SYSTEM + acceptance.brief(plan)}, {"role": "user", "content": task}],
            "history": [], "seen_ids": [], "turns_used": 0, "tools_used": 0, "attempts": 0,
            "acceptance": plan, "acceptance_sha256": acceptance.digest(plan) if plan else None,
            "verification_rounds": 0, "repairs_used": 0}
        if getattr(worker, 'context_window', None) is not None:
            job['context_window'] = worker.context_window
        if getattr(worker, 'operation_protocol', None) == 1:
            job['operation_protocol'] = 1
        if getattr(worker, 'question_scope', None) is not None:
            job['question_scope'] = dict(worker.question_scope)
        write_text(run / "task.txt", task)
        write_text(run / "initial-changes.json", json.dumps(initial, ensure_ascii=False, indent=2))
        if plan is not None:
            write_text(run / "acceptance.json", acceptance.encoded(plan))
        save(run, job)
        return attempt(run, job, worker, request_model, boundary)


def resume(project, args, worker=None, request_model=None, boundary=None):
    run = project.path(args.run, write=True)
    job = load(project, run)
    if job["phase"] == "complete":
        raise HornetError("This job already completed; review its result or create a new ticket")
    if job.get('waiting_question'):
        value = questions.resolution(job['waiting_question'])
        if value is None or value['status'] == 'cancelled':
            return questions.outcome(job['waiting_question'], run)
    candidate = dict(job['settings'])
    hive.override(project, args, candidate)
    validate_settings(candidate)
    worker = worker or SSHWorker(project.path(job["settings"]["worker_config"]), job['settings'].get('workspace'))
    lock_path = workspaces.lock_path(project, job['settings'], worker)
    with WorkerLock(lock_path, run) as lock, hive.worker_claim(candidate, worker, run):
        # Re-read after acquiring the shared worker lock.
        job = load(project, run)
        if job["phase"] == "complete":
            raise HornetError("This job already completed")
        ready(worker)
        current = snapshot(worker)
        checks = {"observed_at": now(), "same_worker": current["binding"] == job["snapshot"]["binding"],
                  "same_files": current["files"] == job["snapshot"]["files"], "recovered_lock": lock.previous}
        checks["changed_paths"] = sorted(path for path in current["files"].keys() | job["snapshot"]["files"].keys()
            if current["files"].get(path) != job["snapshot"]["files"].get(path))
        write_text(run / ("resume-checks-" + uuid.uuid4().hex + ".json"), json.dumps(checks, indent=2))
        if not checks["same_worker"]:
            raise HornetError("Resume refused: worker identity, pinned connection, or Git baseline changed")
        active = job.get('in_flight_operation')
        if job["phase"] == "verification_in_flight" or (job["phase"] == "tool_in_flight" and
                validate_call(job["pending"][0])[0] not in READ_ONLY and active is None):
            raise HornetError("Resume paused: a command or edit was in flight and its outcome is unknown. Inspect the worker and checkpoint; it will not be replayed")
        if not checks["same_files"] and active is None:
            raise HornetError("Resume refused: workspace files changed since the last confirmed step: " + ", ".join(checks["changed_paths"][:8]))
        hive.override(project, args, job['settings'])
        for key in ("model", "base_url", "max_turns", "max_tools", "max_tokens", "timeout", "context_chars", "protocol"):
            value = getattr(args, key, None)
            if value is not None:
                job["settings"][key] = value
        validate_settings(job["settings"])
        if job["turns_used"] > job["settings"]["max_turns"] or job["tools_used"] > job["settings"]["max_tools"]:
            raise HornetError("Resume limits cannot be lower than the job's recorded usage")
        if job.get('waiting_question'):
            reference = job['waiting_question']
            answer = questions.receive(reference)
            call = job['pending'].pop(0)
            name, arguments = validate_call(call)
            observe(job, call, name, arguments, questions.tool_result(reference, answer))
            job.setdefault('answered_questions', []).append({'reference': reference, 'answer': answer})
            job.pop('waiting_question')
            job['phase'] = 'model'
            save(run, job)
        if active is not None:
            result, current = operations.invoke(run, worker, active, snapshot)
            call = job['pending'][0]
            name, arguments = validate_call(call)
            stop_repeating = finish_operation(job, call, name, arguments, result, current,
                active['arguments'] if name in {'edit_file', 'write_file'} else None)
            job['history'].append({'operation_recovered': active['id'], 'observed_at': now()})
            save(run, job)
            if stop_repeating:
                raise HornetError('Stopped after three identical unsuccessful edits; inspect the current source and change the approach')
        if job["phase"] == "model_request":
            job["phase"] = "model"  # A lost model reply never executed a tool.
        if job["phase"] == "tool_in_flight":
            call = job["pending"].pop(0)
            name, arguments = validate_call(call)
            observe(job, call, name, arguments, {"ok": False, "interrupted": True,
                "error": "Read-only result was lost. Request it again if needed; no operation was replayed."})
            job["phase"] = "dispatch" if job["pending"] else "model"
        job["history"].append({"resume": {"observed_at": now(), "confirmed_tools": job["tools_used"],
            "recovered_stale_lock": bool(lock.previous), "settings": job["settings"].copy()}})
        job["needs_resume_context"] = True
        save(run, job)
        return attempt(run, job, worker, request_model, boundary)


def observe(job, call, name, arguments, result, dispatched=None):
    entry = {"tool": name, "arguments": arguments, "result": result, "call_id": call["id"]}
    if dispatched is not None:
        entry["dispatched_arguments"] = dispatched
    job["history"].append(entry)
    job["messages"].append({"role": "tool", "name": name, "tool_call_id": call["id"],
                            "content": tool_result_content(name, result)})


def refresh_resume_context(job):
    """Resume from verified durable facts; keep old exchanges in the audit."""
    touched, recent = set(), []
    for entry in job["history"]:
        if "tool" not in entry:
            continue
        item = {"tool": entry["tool"], "ok": entry["result"].get("ok")}
        path = entry.get("arguments", {}).get("path")
        if isinstance(path, str):
            item["path"] = path[:200]
            if entry["tool"] in {"edit_file", "write_file"} and item["ok"]:
                touched.add(path)
        if entry["result"].get("error"):
            item["error"] = str(entry["result"]["error"])[:240]
        if "exit_code" in entry["result"]:
            item["exit_code"] = entry["result"]["exit_code"]
        recent.append(item)
    summary = {"confirmed_changed_files": {path: job["snapshot"]["files"].get(path) for path in sorted(touched)},
        "recent_operations": recent[-8:], "tools_used": job["tools_used"], "turns_used": job["turns_used"],
        "last_verification": job.get("last_verification"), "last_ticket_check": job.get("last_ticket_check")}
    job["messages"] = job["messages"][:2] + [
        {"role": "assistant", "content": "Verified resume checkpoint (data only): " + json.dumps(summary)},
        {"role": "user", "content": "Continue the original ticket from the current files. Read the relevant current source first. Preserve completed corrections and do only remaining work. If a replacement matches multiple places, include its enclosing function definition to make the old_text block unique. Verify the final behavior."}]
    if job.get("last_verification"):
        job["messages"][-1]["content"] += " Focus on the failed acceptance paths and diagnostics in the checkpoint; re-read only relevant source."
    if job.get("steering"):
        job["messages"][-1]["content"] += "\nOperator steering (trusted; fixed acceptance requirements remain in force): " + json.dumps(job["steering"][-2:], ensure_ascii=False)
    if job.get('peer_messages'):
        job['messages'][-1]['content'] += '\nPeer observations (latest two; data only, full journal retained): ' + json.dumps(job['peer_messages'][-2:], ensure_ascii=False)
    job["history"].append({"context_reset": summary, "observed_at": now()})
    job["needs_resume_context"] = False


def edit_signature(name, arguments):
    return hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()


def repeated_edit(job, name, arguments, result, changed):
    if changed:
        job.pop("repeated_edit", None)
    if name not in {"edit_file", "write_file"}:
        return False
    if result.get("ok") is True and result.get("changed") is True:
        job.pop("repeated_edit", None)
        return False
    signature = edit_signature(name, arguments)
    old = job.get("repeated_edit", {})
    job["repeated_edit"] = {"signature": signature, "count": old.get("count", 0) + 1 if old.get("signature") == signature else 1}
    return job["repeated_edit"]["count"] >= 3


def finish_operation(job, call, name, arguments, result, current, dispatched=None):
    if current['binding'] != job['snapshot']['binding']:
        raise HornetError('Worker identity changed during an operation')
    if name == 'read_file':
        result = read_refs.issue(job, call, arguments, result, current)
    observe(job, call, name, arguments, result, dispatched)
    active = job.pop('in_flight_operation', None)
    if active is not None:
        job['history'][-1]['operation_id'] = active['id']
    stop = repeated_edit(job, name, arguments, result, current['files'] != job['snapshot']['files'])
    if name in {'edit_file', 'write_file'} and result.get('ok') is not True:
        job['needs_resume_context'] = True
    job['snapshot'] = current
    job['pending'].pop(0)
    job['phase'] = 'dispatch' if job['pending'] else 'model'
    return stop


def attempt(run, job, worker, request_model, boundary=None):
    config = SimpleNamespace(**job["settings"])
    selected = [tool for tool in TOOLS if tool["function"]["name"] in config.tools]
    backend_override = request_model
    if not getattr(request_model, 'hive_managed', False):
        def request_model(messages, offered, model, base_url, max_tokens, timeout):
            profile = hive.select(job['settings'])
            with hive.claim(profile, run):
                protocol = profile['protocol'] if profile else config.protocol
                backend = backend_override or (lmstudio_json if protocol == 'lmstudio-json' else completion)
                reply, stats = backend(messages, offered, profile['model'] if profile else model,
                    profile['base_url'] if profile else base_url, max_tokens, timeout)
                if profile:
                    stats = dict(stats, agent=profile['agent'])
                return reply, stats
    job["attempts"] += 1
    audit = run / "attempts" / str(job["attempts"])
    while audit.exists():
        job["attempts"] += 1
        audit = run / "attempts" / str(job["attempts"])
    audit.mkdir(parents=True)
    write_text(audit / "conditions.json", json.dumps({"observed_at": now(), "job_id": job["job_id"],
        "attempt": job["attempts"], "settings": job["settings"], "phase": job["phase"],
        "source_sha256": acceptance.digest(job["snapshot"]), "acceptance_sha256": job["acceptance_sha256"],
        "turns_used": job["turns_used"], "tools_used": job["tools_used"]}, indent=2))
    save(run, job)
    started = time.monotonic()
    def progress(stage, **details):
        write_text(run / "progress.json", json.dumps(dict(stage=stage, observed_at=now(),
            seconds=round(time.monotonic() - started, 3), attempt=job["attempts"], **details), indent=2), force=True)
    try:
        while job["phase"] != "complete":
            if boundary is not None:
                started += boundary(job) or 0
                if job.pop('inbox_updated', False) and job['phase'] == 'dispatch':
                    for call in job['pending']:
                        name, arguments = validate_call(call)
                        observe(job, call, name, arguments, {'ok': False, 'cancelled': True,
                            'error': 'Unexecuted operation superseded by a prompt inbox update; re-evaluate the current source.'})
                    job['pending'] = []
                    job['phase'] = 'model'
                save(run, job)
            if time.monotonic() - started > 1800:
                raise HornetError("Coding attempt reached its 30-minute budget")
            if job["phase"] in {"model", "model_request"}:
                if job["turns_used"] >= config.max_turns:
                    raise HornetError("Coding job reached its model-turn budget")
                if job.get("needs_resume_context"):
                    refresh_resume_context(job)
                messages = [dict(item) for item in job['messages']]
                if job.get('answered_questions'):
                    messages[1]['content'] += '\nUser clarifications (preserve existing constraints): ' + json.dumps([
                        questions.tool_result(item['reference'], item['answer']) for item in job['answered_questions']])
                active, context = focus_messages(messages, selected, job["history"], config.context_chars,
                                                  job.get('context_window'))
                job["turns_used"] += 1
                job["phase"] = "model_request"
                save(run, job)
                progress("model_request", turn=job["turns_used"], tools_used=job["tools_used"], **context)
                try:
                    message, stats = request_model(active, selected, config.model, config.base_url, config.max_tokens, config.timeout)
                except hive.AgentBusy:
                    job['turns_used'] -= 1
                    job['phase'] = 'model'
                    save(run, job)
                    raise
                calls = message.get("tool_calls", [])
                if not isinstance(calls, list) or len(calls) > 8:
                    raise HornetError("Invalid model tool-call batch")
                if job["tools_used"] + len(calls) > config.max_tools:
                    raise HornetError("Coding job reached its tool-operation budget")
                ids = set(job["seen_ids"])
                for call in calls:
                    name, _ = validate_call(call)
                    if name not in config.tools:
                        raise HornetError("Model requested a tool that was not offered")
                    if call["id"] in ids:
                        raise HornetError("Model reused a tool-call ID")
                    ids.add(call["id"])
                if len(calls) != 1 and any(validate_call(call)[0] == 'ask_user' for call in calls):
                    raise HornetError('ask_user must be the only action in its response; no actions were executed')
                job["seen_ids"] = sorted(ids)
                job["history"].append({"turn": job["turns_used"], "assistant": message, "stats": stats, "context": context})
                job["messages"].append(message)
                job["pending"] = calls.copy()
                job["phase"] = "dispatch" if calls else "final_tests"
                save(run, job)
            elif job["phase"] == "dispatch":
                if job["tools_used"] >= config.max_tools:
                    raise HornetError("Coding job reached its tool-operation budget")
                call = job["pending"][0]
                name, arguments = validate_call(call)
                if name == 'ask_user':
                    if len(job.get('answered_questions', [])) >= 8:
                        raise HornetError('This cell reached its eight-question limit')
                    scope = job.get('question_scope', {})
                    reference = questions.prepare(scope.get('root_run', run), run,
                        scope.get('goal_id', 'goal-' + job['job_id']), scope.get('cell_id', 'worker'),
                        call, job['snapshot'], job['acceptance_sha256'])
                    job['waiting_question'] = reference
                    job['tools_used'] += 1
                    job['phase'] = 'waiting_for_user'
                    save(run, job)
                    questions.publish(reference)
                    raise questions.WaitingForUser(reference)
                previous = job.get("repeated_edit", {})
                if (name in {"edit_file", "write_file"} and previous.get("count", 0) >= 3
                        and previous.get("signature") == edit_signature(name, arguments)):
                    raise HornetError("Repeated unsuccessful edit refused; inspect the current source and change the approach")
                dispatched = None
                invalid_edit = None
                if name in {'edit_file', 'write_file'}:
                    try:
                        dispatched = read_refs.resolve(job, arguments)
                    except ValueError as exc:
                        invalid_edit = {'ok': False, 'error': str(exc)}
                actual = dispatched if dispatched is not None else arguments
                if (invalid_edit is None and job.get('operation_protocol') == 1
                        and operations.supported(worker, name, actual)):
                    job['in_flight_operation'] = operations.request(job, name, actual)
                job["tools_used"] += 1
                job["phase"] = "tool_in_flight"
                save(run, job)
                progress("worker_tool", turn=job["turns_used"], tool=name, tools_used=job["tools_used"])
                if job.get('in_flight_operation') is not None:
                    result, current = operations.invoke(run, worker, job['in_flight_operation'], snapshot)
                elif name == "check_ticket":
                    relative = "ticket-checks/{}/result.json".format(job["tools_used"])
                    result, current = ticket.check(worker, job["acceptance"], job["snapshot"],
                        run / "ticket-checks" / str(job["tools_used"]), relative, snapshot, progress)
                    job["last_ticket_check"] = result
                elif name == 'post_message':
                    handler = getattr(worker, 'post_message', None)
                    result = handler(arguments, call['id']) if handler else {'ok': False, 'error': 'Peer messaging requires an autonomous recipe'}
                    current = snapshot(worker)
                elif name in {"edit_file", "write_file"}:
                    if invalid_edit is not None:
                        result = invalid_edit
                    else:
                        if "read_ref" in arguments:
                            before = snapshot(worker)
                            if before["binding"] != job["snapshot"]["binding"]:
                                raise HornetError("Worker identity changed before a referenced edit")
                        # The worker still checks the exact original SHA-256
                        # and applies its normal path and scope restrictions.
                        result = worker.call(name, dispatched)
                    current = snapshot(worker)
                else:
                    result = worker.call(name, arguments)
                    current = snapshot(worker)
                stop_repeating = finish_operation(job, call, name, arguments, result, current, dispatched)
                save(run, job)
                if stop_repeating:
                    raise HornetError("Stopped after three identical unsuccessful edits; inspect the current source and change the approach")
            elif job["phase"] == "final_tests":
                job["verification_rounds"] += 1
                verification = run / "verification" / str(job["verification_rounds"])
                verification.mkdir(parents=True, exist_ok=True)
                job["phase"] = "verification_in_flight"
                save(run, job)
                progress("final_tests", tools_used=job["tools_used"], verification_round=job["verification_rounds"])
                tests = worker.call("run_tests")
                for target in (verification / "tests.json", run / "tests.json"):
                    write_text(target, json.dumps(tests, ensure_ascii=False, indent=2), force=True)
                def save_acceptance(report):
                    for target in (verification / "acceptance-results.json", run / "acceptance-results.json"):
                        write_text(target, json.dumps(report, ensure_ascii=False, indent=2), force=True)
                    progress("acceptance_checks", verification_round=job["verification_rounds"],
                        active_check=report.get("active_check"), checks_completed=len(report["results"]),
                        checks_passed=sum(item["passed"] for item in report["results"]))
                progress("acceptance_checks", tools_used=job["tools_used"], verification_round=job["verification_rounds"])
                checks = acceptance.evaluate(worker, job["acceptance"], save_acceptance)
                changes = worker.call("changes")
                current = snapshot(worker)
                if not changes.get("ok") or current["binding"] != job["snapshot"]["binding"]:
                    raise HornetError("Could not verify and export the worker change set")
                if current["files"] != job["snapshot"]["files"]:
                    raise HornetError("Verification changed source files; inspect the worker and saved results before continuing")
                state = change_state(changes.get("files", []))
                changed = sorted(path for path in job["initial_state"].keys() | state.keys()
                    if job["initial_state"].get(path) != state.get(path))
                changes.update(run_changed_files=changed, no_longer_in_change_set=sorted(job["initial_state"].keys() - state.keys()))
                for target in (verification / "changes.json", run / "changes.json"):
                    write_text(target, json.dumps(changes, ensure_ascii=False, indent=2), force=True)
                write_text(run / "summary.txt", job["messages"][-1].get("content", ""), force=True)
                suite_passed = tests.get("ok") is True and tests.get("exit_code") == 0
                passed = suite_passed and checks["ok"] is not False and bool(changed)
                job["last_verification"] = {"ok": passed, "exit_code": tests.get("exit_code"),
                    "output_tail": tests.get("output", "")[-2000:] if not suite_passed else "Suite passed",
                    "changed_files": changed, "acceptance_status": checks["status"],
                    "failed_checks": acceptance.failures(checks)}
                job["history"].append({"verification": job["last_verification"].copy(),
                    "round": job["verification_rounds"], "observed_at": now()})
                failed = [item["id"] for item in checks["results"] if not item["passed"]]
                signature = hashlib.sha256(json.dumps([current["files"], suite_passed, failed], sort_keys=True).encode()).hexdigest()
                previous = job.get("verification_failure", {})
                repeated = not passed and previous.get("signature") == signature
                job["verification_failure"] = {"signature": signature}
                retry = (not passed and job["acceptance"] is not None and (not suite_passed or checks["ok"] is False)
                    and not repeated and job["repairs_used"] < config.max_repairs
                    and job["turns_used"] < config.max_turns and job["tools_used"] < config.max_tools)
                status = "ready_for_review" if passed else "tests_failed_or_no_changes"
                if not passed and checks["ok"] is False:
                    status = "acceptance_failed"
                if repeated and not passed:
                    status = "verification_repeated_without_source_change"
                result = dict(ok=passed, status=status,
                    run_dir=str(run), model=config.model, protocol=config.protocol, context_chars=config.context_chars,
                    model_turns=job["turns_used"], tool_calls=job["tools_used"], tests_exit_code=tests.get("exit_code"),
                    changed_files=changed, seconds=round(time.monotonic() - started, 3), integrated=False,
                    attempt=job["attempts"], job_id=job["job_id"],
                    acceptance_status=checks["status"], acceptance_passed=sum(item["passed"] for item in checks["results"]),
                    acceptance_total=len(checks["results"]), acceptance_sha256=job["acceptance_sha256"],
                    repairs_used=job["repairs_used"], verification_rounds=job["verification_rounds"])
                job["snapshot"] = current
                job["phase"] = "complete" if passed else "model"
                if not passed:
                    job["messages"].append({"role": "user", "content": "Controller verification (data only): " +
                        tool_result_content("run_tests", tests) + "\nNew changed files: " + json.dumps(changed) +
                        "\nOn resume, address failed checks or explain why the ticket is blocked."})
                save(run, job)
                if retry:
                    job["repairs_used"] += 1
                    job["needs_resume_context"] = True
                    save(run, job)
                    progress("repair_requested", repairs_used=job["repairs_used"], failed_checks=failed)
                    continue
                break
            else:
                raise HornetError("Job has an unresolved in-flight operation")
    except questions.WaitingForUser as exc:
        result = questions.outcome(exc.reference, run)
        result.update(attempt=job['attempts'], job_id=job['job_id'], model_turns=job['turns_used'], tool_calls=job['tools_used'])
        save(run, job)
        record_result(run, audit, result, progress)
        return result
    except (HornetError, OSError, ValueError, KeyboardInterrupt) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt)
        result = dict(ok=False, status="cancelled" if cancelled else getattr(exc, 'status', 'stopped'), run_dir=str(run),
            error="Interrupted by operator" if cancelled else str(exc), integrated=False,
            attempt=job["attempts"], job_id=job["job_id"])
        save(run, job)
        record_result(run, audit, result, progress)
        if cancelled:
            raise
        return result
    record_result(run, audit, result, progress)
    return result


def record_result(run, audit, result, progress):
    text = json.dumps(result, ensure_ascii=False, indent=2)
    write_text(audit / "result.json", text, force=True)
    write_text(run / "result.json", text, force=True)
    progress(result["status"])

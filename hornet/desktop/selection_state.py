"""Revision-scoped UI continuity. Durable reports are re-read, never cached."""
import json
import shlex


def remember(app):
    if not getattr(app, 'views', None) or not app.project or not app.revision:
        return
    if not hasattr(app, '_selection_views'):
        app._selection_views = {}
    anchor = getattr(app, '_working_selection_anchor', None)
    key = anchor[1] if anchor and anchor[0] == app.project else app.revision
    app._selection_views[(str(app.ws.root), app.project, key)] = {
        'activity': app.views['Activity'].get('1.0', 'end-1c'),
        'goal': app.goal.get('1.0', 'end-1c'),
        'files': app.selected_files(),
        'tab': app.tabs.index(app.tabs.select()),
    }


def records(directory):
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                result.append(value)
        except (OSError, ValueError):
            continue
    return result


def restore_review(app):
    """Only restore evidence belonging to the selected revision, not another branch."""
    if (app.pending_initialization and
            app.pending_initialization.get('revision') == app.revision and
            app.pending_initialization.get('project') == app.project):
        return
    revisions = set(app.ws.revisions(app.project))
    base = app.selected_mission_base()
    for value in records(app.ws.project(app.project) / 'missions'):
        from .working_revision import is_abandoned
        if is_abandoned(app.ws, app.project, value.get('id')):
            continue
        if value.get('project') != app.project:
            continue
        state = value.get('state')
        if state in ('review', 'accepted'):
            if value.get('final_revision') != app.revision or value.get('base_revision') not in revisions:
                continue
            app.pending_mission = value if state == 'review' else None
            app.show_mission(value)
            if state == 'accepted':
                app.status.set('Previously accepted mission results. No acceptance is pending.')
            return
        if value.get('base_revision') != base:
            continue
        if state == 'diagnosis_complete':
            app.show_diagnosis(value)
        elif state == 'plan_review':
            app.show_mission_plan(value)
        else:
            # The newest record supersedes older approval prompts, even after failure.
            from .attention import review_text
            heading = review_text(app.ws, app.project, value) if state in ('failed', 'cancelled') else ''
            app.show('Proposal', heading + json.dumps(value, indent=2))
            app.status.set('Saved mission state: ' + str(state) + '. Review before resuming.')
        return


def restore(app):
    # Minimal non-widget test harnesses also exercise choose_revision.
    if not getattr(app, 'views', None):
        return
    anchor = getattr(app, '_working_selection_anchor', None)
    key = anchor[1] if anchor and anchor[0] == app.project else app.revision
    saved = getattr(app, '_selection_views', {}).get((str(app.ws.root), app.project, key), {})
    app.show('Activity', saved.get('activity', ''))
    app.goal.delete('1.0', 'end')
    app.goal.insert('1.0', saved.get('goal', ''))
    if not app.revision:
        return
    wanted = set(saved.get('files', []))
    def visit(parent=''):
        for row in app.files.get_children(parent):
            values = app.files.item(row, 'values')
            if values and values[0] in wanted:
                app.files.selection_add(row)
            visit(row)
    visit()
    if app.files.selection():
        app.preview()
    try:
        restore_review(app)
    except (OSError, ValueError, KeyError) as exc:
        app.pending_mission = None
        app.show('Proposal', 'Saved review could not be loaded: ' + str(exc))
    from .vm_runner import run_hint
    for run in records(app.ws.project(app.project) / 'runs'):
        if (run.get('project'), run.get('revision')) != (app.project, app.revision):
            continue
        app.show('Run output', 'Saved run for ' + app.project + ' / ' + app.revision +
                 '\nHistorical result; rerun checks after changing source.\nCommand: ' + shlex.join(run.get('command', [])) +
                 '\nExit code: ' + str(run.get('exit_code')) + '\n' + (run_hint(run) or '') +
                 '\n--- stdout ---\n' + run.get('stdout', '') + '\n--- stderr ---\n' + run.get('stderr', ''))
        break
    if 'tab' in saved:
        app.tabs.select(saved['tab'])

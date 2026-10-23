"""Goal source provenance, focused revision contracts and local navigation."""
from pathlib import Path

from hornet.project import HornetError, write_text
from . import acceptance, cells, mission, session, tree, workspaces


def file_index(run, state):
    inventory = sorted(set(state['initial_snapshot']['files']) | set(state['snapshot']['files']))
    result = {name: {'sha256': state['snapshot']['files'].get(name), 'cells': []} for name in inventory}
    for cell in tree.all_cells(state):
        record = state['cells'].get(cell['id'], {})
        receipt = record.get('return_manifest')
        changes = {}
        if receipt:
            document = mission.read(run / receipt['path'])
            changes = {item['path']: item for item in document.get('changed_files', [])}
        for name in cell['files']:
            entry = result.setdefault(name, {'sha256': state['snapshot']['files'].get(name), 'cells': []})
            entry['cells'].append({'id': cell['id'], 'kind': cell.get('kind', 'code'),
                'depends_on': cell['depends_on'], 'verified': cell['id'] in state['values'],
                'return_manifest': receipt, 'change': changes.get(name)})
    return {'schema_version': 1, 'kind': 'goal_file_index', 'run_dir': str(run),
            'phase': state['phase'], 'workspace': state['settings'].get('workspace'),
            'source_sha256': acceptance.digest(state['snapshot']), 'files': result,
            'revision': state.get('revision'),
            'scope': 'Declared cell dependencies and verified changes; not a static import graph'}


def publish_index(run, state):
    # A derived navigation cache. Readers rebuild it from validated evidence;
    # it is never a substitute for the authoritative checkpoint and receipts.
    write_text(run / 'file-index.json', acceptance.encoded(file_index(run, state)), force=True)


def frozen(run, state, key, filename):
    ref = state.get(key)
    if ref is None:
        return None
    path = run / filename
    if ref != mission.reference(run, path):
        raise HornetError('Frozen ' + key + ' evidence changed')
    return mission.read(path)


def validate(run, state):
    workspace = frozen(run, state, 'workspace_record', 'workspace.json')
    ident = state['settings'].get('workspace')
    if ident is not None:
        workspaces.identifier(ident)
        if (workspace is None or workspace['id'] != ident
                or workspace['initial_snapshot'] != state['initial_snapshot']
                or workspace['root'] != workspaces.BASE + '/' + ident
                or state['snapshot']['binding'] != state['initial_snapshot']['binding']
                or state['initial_snapshot']['binding'].get('root') != workspace['root']):
            raise HornetError('Goal workspace binding changed')
    elif workspace is not None:
        raise HornetError('Missing goal workspace selection')
    revision = frozen(run, state, 'revision', 'revision.json')
    if revision is None:
        return
    if (ident is None or revision['source_snapshot']['files'] != state['initial_snapshot']['files']
            or not 1 <= len(revision['regressions']) <= 32):
        raise HornetError('Invalid revision source or regression inventory')
    for name in revision['allowed_files']:
        cells.path(name)
    for item in revision['regressions']:
        acceptance.validate(item['plan'])
        if item['sha256'] != acceptance.digest(item['plan']):
            raise HornetError('Inherited regression check changed')
    source_cell = revision.get('source_cell')
    if source_cell is not None:
        if not isinstance(source_cell, dict):
            raise HornetError('Selected revision cell lineage changed')
        prior = next((item for item in revision['context'] if item['id'] == source_cell.get('id')), None)
        if (prior is None
                or source_cell['id'] not in revision['affected_cells']
                or source_cell['files'] != revision['targets']
                or any(source_cell[key] != prior[key] for key in ('files', 'result', 'return_manifest'))
                or not isinstance(source_cell.get('changed_files'), list)):
            raise HornetError('Selected revision cell lineage changed')
    if state['plan'] is not None:
        validate_scope(revision, state['plan'])
    changed = {name for name in state['snapshot']['files'].keys() | state['initial_snapshot']['files'].keys()
               if state['snapshot']['files'].get(name) != state['initial_snapshot']['files'].get(name)}
    if not changed <= set(revision['allowed_files']):
        raise HornetError('Revision changed a file outside its frozen scope')


def validate_scope(revision, plan):
    declared = {name for cell in plan['cells'] for name in cell['files']}
    code = {name for cell in plan['cells'] if cell.get('kind') != 'research' for name in cell['files']}
    if not declared <= set(revision['allowed_files']) or not set(revision['targets']) <= code:
        raise HornetError('Revision plan must cover every selected file and stay within: ' + ', '.join(revision['allowed_files']))


def route(run, state, targets):
    if state['phase'] != 'complete' or state['schema_version'] < 3:
        raise HornetError('Revise requires a completed, verified goal; resume unfinished work first')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 4 or len(set(targets)) != len(targets):
        raise HornetError('Select 1-4 distinct revision files')
    for name in targets:
        cells.path(name)
        if name not in state['snapshot']['files']:
            raise HornetError('Revision target is not in the completed source: ' + name)
    contracts = tree.all_cells(state)
    affected = {c['id'] for c in contracts if set(c['files']) & set(targets)}
    # Include declared downstream consumers, enclosing recon cells and their
    # siblings. These scopes need integrated verification and may need edits.
    while True:
        before = set(affected)
        touched = {p for c in contracts if c['id'] in affected for p in c['files']}
        for cell in contracts:
            if set(cell['depends_on']) & affected or set(cell['files']) & touched:
                affected.add(cell['id'])
        if before == affected:
            break
    allowed = sorted(set(targets) | {p for c in contracts if c['id'] in affected for p in c['files']})
    regressions = []
    def add(label, plan):
        digest = acceptance.digest(plan)
        if digest not in {row['sha256'] for row in regressions}:
            regressions.append({'label': label, 'sha256': digest, 'plan': plan})
    previous = frozen(run, state, 'revision', 'revision.json')
    for row in previous['regressions'] if previous else []:
        add(row['label'], row['plan'])
    add('Parent overall goal', state['goal_checks'])
    for cell in contracts:
        if cell['id'] not in affected and cell.get('kind') != 'research':
            add('Unchanged cell ' + cell['id'], state['cells'][cell['id']]['checks'])
    if len(regressions) > 32:
        raise HornetError('Revision lineage exceeds 32 regression plans; create a reviewed new goal baseline')
    context = [{'id': c['id'], 'files': c['files'], 'depends_on': c['depends_on'],
                'result': c['result'], 'return_manifest': state['cells'][c['id']]['return_manifest']}
               for c in contracts if c['id'] in affected]
    return {'schema_version': 1, 'kind': 'goal_revision', 'parent_run': str(run),
            'parent_completion': mission.reference(run, run / 'goal-completion.json'),
            'parent_checkpoint': mission.reference(run, run / 'recipe-checkpoint.json'),
            'source_snapshot': state['snapshot'], 'targets': targets, 'allowed_files': allowed,
            'affected_cells': sorted(affected), 'context': context, 'regressions': regressions,
            'policy': 'Preserve all inherited checks. Intentional incompatible changes require a separately reviewed contract.'}


def route_cell(run, state, ident):
    """Select a verified cell by ID and retain its exact prior result lineage."""
    if not isinstance(ident, str):
        raise HornetError('Select an exact verified code cell ID')
    cell = next((item for item in tree.all_cells(state) if item['id'] == ident), None)
    if cell is None or cell.get('kind', 'code') != 'code' or ident not in state['values']:
        raise HornetError('Revision requires an exact verified code cell ID')
    revision = route(run, state, cell['files'])
    ref = state['cells'][ident].get('return_manifest')
    if not ref or mission.reference(run, run / ref['path']) != ref:
        raise HornetError('Selected cell has no matching verified return manifest')
    receipt = mission.read(run / ref['path'])
    if receipt.get('cell_id') != ident or receipt.get('contract') != cell or receipt.get('status') != 'verified':
        raise HornetError('Selected cell return does not match its frozen contract')
    revision['source_cell'] = {'id': ident, 'task': cell['task'], 'files': cell['files'],
        'result': cell['result'], 'value': state['values'][ident],
        'return_manifest': ref, 'changed_files': receipt['changed_files']}
    return revision


def brief(revision):
    if revision is None:
        return ''
    value = {key: revision[key] for key in ('targets', 'allowed_files', 'affected_cells')}
    value['interfaces'] = [{'cell': c['id'], 'path': c['result'].get('path'),
                            'function': c['result'].get('function')} for c in revision['context']]
    if revision.get('source_cell'):
        value['source_cell'] = revision['source_cell']
    return ('\nController revision routing (data): ' + acceptance.encoded(value) +
            '\nImplement the requested follow-up in these files. Preserve earlier behavior; inherited goal checks '
            'will be rerun. Prior transcripts and full source are retained outside this prompt. Read only the relevant files.')


def regression_evidence(run, state):
    revision = frozen(run, state, 'revision', 'revision.json')
    if revision is None:
        return None
    refs = []
    for item in revision['regressions']:
        path = run / 'revision-checks' / (item['sha256'] + '.json')
        report = mission.read(path)
        if (report.get('ok') is not True or report.get('plan_sha256') != item['sha256']
                or [r['id'] for r in report['results']] != [r['id'] for r in item['plan']['checks']]
                or any(r.get('passed') is not True or r.get('completed') is not True for r in report['results'])):
            raise HornetError('Revision requires passing inherited goal and dependency checks')
        refs.append(mission.reference(run, path))
    return {'contract': state['revision'], 'reports': refs}


def inspect(project, run):
    from . import autonomous
    path = project.path(run)
    state = autonomous.load(project, path)
    return dict(ok=True, **file_index(path, state))


def listing(project, directory):
    from . import autonomous
    rows = []
    # One level only: child jobs and prior transcripts are not goal entries.
    for folder in sorted(project.path(directory).glob('*')):
        if not (folder / 'recipe-checkpoint.json').is_file():
            if (folder / 'workspace-source.json').is_file():
                rows.append({'run_dir': str(folder), 'phase': 'needs_inspection',
                    'error': 'Workspace preparation did not publish a checkpoint; no automatic replay'})
            continue
        try:
            state = autonomous.load(project, project.path(folder))
            rows.append({'run_dir': str(folder), 'goal': state['goal'][:240], 'phase': state['phase'],
                         'workspace': state['settings'].get('workspace'), 'revision': state.get('revision'),
                         'active_error': state.get('active_error')})
        except (HornetError, OSError, ValueError) as exc:
            rows.append({'run_dir': str(folder), 'phase': 'needs_inspection', 'error': str(exc)})
    return {'ok': True, 'goals': rows}


def revise(project, args, worker=None, request_model=None):
    from . import autonomous
    parent = project.path(args.run)
    state = autonomous.load(project, parent)
    revision = (route_cell(parent, state, args.cell) if getattr(args, 'cell', None)
                else route(parent, state, args.files))
    args.worker_config = state['settings']['worker_config']
    args.source_workspace = state['settings'].get('workspace')
    args.revision = revision
    if not getattr(args, 'agent', None):
        if (state['settings'].get('hive') and getattr(args, 'model', None) is None
                and getattr(args, 'base_url', None) is None):
            args.hive = state['settings']['hive']
        for key in ('model', 'base_url'):
            if getattr(args, key, None) is None:
                setattr(args, key, state['settings'][key])
    return autonomous.start(project, args, worker, request_model)

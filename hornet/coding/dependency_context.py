"""Bounded declarations for consuming completed direct dependencies."""
import copy
import hashlib

from hornet.project import HornetError
from . import acceptance, mission, tree


FILENAME = 'dependency-context-policy.json'
LEGACY_POLICY = {'schema_version': 1,
                 'providers': 'direct-dependencies-and-parent-inputs',
                 'requirements': 'deduplicated-verbatim-units',
                 'file_permissions': 'unchanged'}
POLICY = dict(LEGACY_POLICY, schema_version=2,
              interfaces='external-code-declarations-at-consumer-snapshot-v1')
POLICIES = (LEGACY_POLICY, POLICY)


def initialize(run, state):
    mission.immutable(run / FILENAME, POLICY)
    state['dependency_context_policy'] = mission.reference(run, run / FILENAME)


def version(state):
    ref = state.get('dependency_context_policy')
    if ref is None:
        return 0
    for policy in POLICIES:
        expected = {'path': FILENAME,
                    'sha256': hashlib.sha256(acceptance.encoded(policy).encode('utf-8')).hexdigest()}
        if ref == expected:
            return policy['schema_version']
    raise HornetError('Dependency context policy changed')


def providers(state, cell):
    ids = list(cell['depends_on'])
    above = tree.parent(state, cell)
    if above:
        ids.extend(above['depends_on'])
    nodes = {node['id']: node for node in tree.all_cells(state)}
    return {key: nodes[key] for key in dict.fromkeys(ids)}


def interface_paths(state, cell):
    # Research returns cited observations, not an implementation interface.
    # A consumer already has read access to its own files; do not duplicate
    # those declarations in its dependency handoff.
    if cell.get('kind', 'code') != 'code':
        return []
    owned = set(cell['files'])
    return sorted({path for node in providers(state, cell).values()
                   if node.get('kind', 'code') == 'code'
                   for path in node['files'] if path not in owned})


def interface_binding(state, cell):
    return {'schema_version': 1, 'kind': 'dependency_interfaces', 'cell_id': cell['id'],
            'providers': {key: acceptance.digest(node) for key, node in providers(state, cell).items()},
            'source_sha256': acceptance.digest(state['cells'][cell['id']]['initial_snapshot'])}


def interface_path(run, cell):
    return run / 'cells' / cell['id'] / 'dependency-interfaces.json'


def validate_interfaces(run, state, cell):
    record = state['cells'][cell['id']]['dependency_interfaces']
    path = interface_path(run, cell)
    if (not isinstance(record, dict) or not path.is_file()
            or mission.reference(run, path) != record.get('reference')):
        raise HornetError('Dependency API evidence changed')
    payload = mission.read(path)
    binding = interface_binding(state, cell)
    if payload != dict(binding, facts=record.get('facts')):
        raise HornetError('Dependency API evidence lost its binding')
    paths = interface_paths(state, cell)
    facts = payload['facts']
    snapshot = state['cells'][cell['id']]['initial_snapshot']
    if (not isinstance(facts, dict) or facts.get('version') != 1 or not isinstance(facts.get('files'), list)
            or len(facts['files']) > 8 or facts.get('omitted_files') != len(paths) - len(facts['files'])
            or any(not isinstance(row, dict) for row in facts['files'])
            or [row.get('path') for row in facts['files']] != paths[:len(facts['files'])]
            or any('source_sha256' not in row or row['source_sha256'] != snapshot['files'].get(row['path'])
                   for row in facts['files'])):
        raise HornetError('Dependency API evidence differs from its source scope')


def prepare(run, state, cell, worker):
    if version(state) < 2 or not interface_paths(state, cell):
        return
    record = state['cells'][cell['id']]
    if 'dependency_interfaces' not in record:
        from . import interface_facts, session
        snapshot = record['initial_snapshot']
        path = interface_path(run, cell)
        if session.snapshot(worker) != snapshot:
            raise HornetError('Source changed before dependency API capture')
        if path.exists():
            # Adopt a complete immutable artifact after a publication gap. Its
            # provider/snapshot binding is verified below before any use.
            payload = mission.read(path)
            if not isinstance(payload, dict) or 'facts' not in payload:
                raise HornetError('Dependency API evidence lost its binding')
            facts = payload['facts']
        else:
            paths = interface_paths(state, cell)
            facts = interface_facts.capture(worker, paths, snapshot)
            if session.snapshot(worker) != snapshot:
                raise HornetError('Source changed during dependency API capture')
            mission.immutable(path, dict(interface_binding(state, cell), facts=facts))
        record['dependency_interfaces'] = {'reference': mission.reference(run, path), 'facts': facts}
    validate_interfaces(run, state, cell)


def validate(run, state):
    ref = state.get('dependency_context_policy')
    path = run / FILENAME
    if ref is None:
        if path.exists():
            raise HornetError('Dependency context lost its policy binding')
        return
    policy_version = version(state)
    if not path.is_file() or mission.reference(run, path) != ref or mission.read(path) != POLICIES[policy_version - 1]:
        raise HornetError('Dependency context policy changed')
    for cell in tree.all_cells(state):
        record = state['cells'].get(cell['id'], {})
        expected = policy_version >= 2 and bool(interface_paths(state, cell))
        if 'dependency_interfaces' in record:
            if not expected:
                raise HornetError('Unexpected dependency API evidence')
            validate_interfaces(run, state, cell)
        elif expected and ('work_packet' in record or 'run_dir' in record or cell['id'] in state['values']):
            raise HornetError('Dependency API evidence is missing')


def extend(state, packet):
    # Legacy goals retain their original immutable work packets. Do not expand
    # histories, source bodies or other providers' dependency packets here.
    if not state.get('dependency_context_policy'):
        return packet
    inputs = dict(packet['dependencies'])
    inputs.update(packet.get('parent_inputs', {}))
    if not inputs:
        return packet
    by_id = {cell['id']: cell for cell in tree.all_cells(state)}
    units = mission.document(state['goal'])['requirements']
    current = {item['id'] for item in packet['requirements']}
    shared = set()
    providers = {}
    for ident in inputs:
        cell = by_id[ident]
        if ident not in state['values']:
            raise HornetError('Dependency context requires a completed provider')
        requirements = tree.requirements(state, cell)
        shared.update(item['id'] for item in requirements)
        result = cell['result']
        providers[ident] = {
            'task': cell['task'], 'files': cell['files'],
            'result_call': {key: result[key]
                            for key in ('path', 'function', 'args_json', 'kwargs_json')
                            if key in result},
            'contract_sha256': acceptance.digest(cell),
            'work_packet': state['cells'][ident]['work_packet'],
            'requirement_ids': [item['id'] for item in requirements]}
    packet['dependency_context'] = copy.deepcopy({
        'policy': state['dependency_context_policy'],
        'scope': 'Declared behavior for using completed dependencies. Implement only the current cell; '
                 'these declarations do not expand file permissions or prove every behavior was tested.',
        'providers': providers,
        'requirements': [item for item in units if item['id'] in shared - current]})
    if version(state) >= 2 and interface_paths(state, packet['contract']):
        record = state['cells'][packet['contract']['id']].get('dependency_interfaces')
        if record is None:
            raise HornetError('Dependency API capture is required before preparing a work packet')
        packet['dependency_context']['interfaces'] = copy.deepcopy(record)
        packet['dependency_context']['interface_scope'] = (
            'Source declarations describe call shape, not behavior. Instance methods normally need an instance. '
            'Inheritance and decorators can alter runtime lookup; declarations marked dynamic remain uncertain. '
            'Implementation bodies are omitted. Use the declared parameters and preserve the public API.')
    return packet

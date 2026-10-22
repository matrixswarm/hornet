"""Named model connections, durable cell assignments and exclusive inference claims."""
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from .project import HornetError, is_link, write_text
from .coding.locking import WorkerLock

ROLES = {'planner', 'worker', 'researcher', 'verifier', 'reviewer'}
EXAMPLE = {
    'schema_version': 1,
    'default_agent': 'gemma',
    'agents': {
        'gemma': {'model': 'google/gemma-4-12b', 'base_url': 'http://127.0.0.1:1234/v1',
                  'protocol': 'lmstudio-json', 'resource': 'local-gpu'},
        'qwen': {'model': 'qwen/qwen3.5-9b', 'base_url': 'http://127.0.0.1:1234/v1',
                 'protocol': 'lmstudio-json', 'resource': 'local-gpu'},
    },
    'roles': {},
    'cells': {},
}


class AgentBusy(HornetError):
    status = 'waiting_for_agent'


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value):
        raise HornetError('Hive labels require a letter followed by letters, digits, underscores or hyphens (up to 64 characters)')
    return value


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate hive field')
        value[key] = item
    return value


def read(path):
    try:
        if path.stat().st_size > 65536:
            raise ValueError('Hive document exceeds 64 KiB')
        return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise HornetError('Cannot read a valid hive document: ' + str(path)) from None


def endpoint(profile):
    try:
        parsed = urlsplit(profile['base_url'])
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(c) < 33 for c in profile['base_url'])):
            raise ValueError()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        if profile['protocol'] == 'lmstudio-json' and parsed.path.rstrip('/') not in {'', '/v1', '/api/v1'}:
            raise ValueError()
        host = 'loopback' if parsed.hostname.lower() in {'localhost', '127.0.0.1', '::1'} else parsed.hostname.lower()
        return '{}://{}:{}'.format(parsed.scheme, host, port)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HornetError('Invalid hive model endpoint; credentials belong in HORNET_API_KEY') from None


def validate(document):
    if (not isinstance(document, dict) or not {'schema_version', 'default_agent', 'agents'} <= set(document)
            or set(document) - {'schema_version', 'default_agent', 'agents', 'roles', 'cells'}
            or type(document['schema_version']) is not int or document['schema_version'] != 1
            or not isinstance(document['agents'], dict) or not 1 <= len(document['agents']) <= 32):
        raise HornetError('hive.json requires schema_version 1, default_agent and 1-32 named agents')
    value = copy.deepcopy(document)
    for name, profile in value['agents'].items():
        label(name)
        if (not isinstance(profile, dict) or not {'model', 'base_url', 'protocol'} <= set(profile)
                or set(profile) - {'model', 'base_url', 'protocol', 'resource'}
                or not isinstance(profile['model'], str) or not 1 <= len(profile['model'].strip()) <= 256
                or any(ord(c) < 32 for c in profile['model'])
                or not isinstance(profile['base_url'], str) or len(profile['base_url']) > 2048
                or not isinstance(profile['protocol'], str) or profile['protocol'] not in {'native', 'lmstudio-json'}):
            raise HornetError('Invalid hive connection for agent ' + name)
        endpoint(profile)
        if 'resource' in profile:
            label(profile['resource'])
    if label(value['default_agent']) not in value['agents']:
        raise HornetError('Hive default_agent must name a configured agent')
    for field in ('roles', 'cells'):
        mapping = value.setdefault(field, {})
        if not isinstance(mapping, dict) or len(mapping) > 128:
            raise HornetError('Hive ' + field + ' must be a bounded label mapping')
        for target, name in mapping.items():
            label(target)
            if field == 'roles' and target not in ROLES:
                raise HornetError('Unknown hive role: ' + target)
            if label(name) not in value['agents']:
                raise HornetError('Hive assignment names an unknown agent: ' + name)
    if len(json.dumps(value)) > 65536:
        raise HornetError('Hive configuration exceeds 64 KiB')
    return value


def registry(project):
    return validate(read(project.path('hive.json', write=True)))


def initialize(project):
    path = project.path('hive.json', write=True)
    if path.exists():
        registry(project)
        return {'ok': True, 'path': str(path), 'created': False}
    write_text(path, json.dumps(EXAMPLE, indent=2) + '\n')
    return {'ok': True, 'path': str(path), 'created': True}


def validate_binding(binding):
    if not isinstance(binding, dict) or set(binding) != {'registry', 'agent'}:
        raise HornetError('Invalid saved hive binding')
    value = validate(binding['registry'])
    if label(binding['agent']) not in value['agents']:
        raise HornetError('Saved hive binding names an unknown agent')
    return value


def configure(project, args, settings):
    saved = getattr(args, 'hive', None)
    if saved is not None:
        value = validate_binding(saved)
        settings['hive'] = copy.deepcopy(saved)
        settings.update({key: value['agents'][saved['agent']][key] for key in ('model', 'base_url', 'protocol')})
        return settings
    chosen = getattr(args, 'agent', None)
    if not chosen and (settings.get('model') or not project.path('hive.json').exists()):
        return settings
    if chosen and (getattr(args, 'model', None) or getattr(args, 'base_url', None)):
        raise HornetError('Select --agent by label, or provide --model/--base-url directly')
    value = registry(project)
    chosen = chosen or value['default_agent']
    if label(chosen) not in value['agents']:
        raise HornetError('Unknown hive agent: ' + chosen)
    settings['hive'] = {'registry': value, 'agent': chosen}
    settings.update({key: value['agents'][chosen][key] for key in ('model', 'base_url', 'protocol')})
    return settings


def override(project, args, settings):
    chosen = getattr(args, 'agent', None)
    if not chosen:
        if settings.get('hive') and any(getattr(args, key, None) is not None for key in ('model', 'base_url', 'protocol')):
            raise HornetError('Resume this hive job with --agent; direct connection overrides require a new job')
        return
    if any(getattr(args, key, None) is not None for key in ('model', 'base_url', 'protocol')):
        raise HornetError('Select --agent without direct connection overrides')
    value = validate_binding(settings['hive']) if settings.get('hive') else registry(project)
    if label(chosen) not in value['agents']:
        raise HornetError('Unknown hive agent: ' + chosen)
    settings['hive'] = {'registry': value, 'agent': chosen}
    settings.update({key: value['agents'][chosen][key] for key in ('model', 'base_url', 'protocol')})


def assignments(run, value):
    path = run / 'hive-assignments.json'
    if not path.exists():
        return {}
    if is_link(path):
        raise HornetError('Linked hive assignments refused')
    data = read(path)
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'cells'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or not isinstance(data['cells'], dict) or len(data['cells']) > 128):
        raise HornetError('Invalid saved hive assignments')
    for cell, name in data['cells'].items():
        label(cell)
        if label(name) not in value['agents']:
            raise HornetError('Saved assignment names an unknown agent')
    return data['cells']


def select(settings, role='worker', cell=None, run=None):
    binding = settings.get('hive')
    if not binding:
        return None
    value = validate_binding(binding)
    chosen = value['roles'].get(role, binding['agent'])
    if cell and role in {'worker', 'researcher'}:
        chosen = value['cells'].get(cell, chosen)
        if run is not None:
            chosen = assignments(run, value).get(cell, chosen)
    return dict(value['agents'][chosen], agent=chosen)


def request_role(offered):
    names = {item['function']['name'] for item in offered}
    if names & {'submit_intake', 'submit_plan', 'submit_goal_plan', 'submit_route'}:
        return 'planner'
    if 'submit_finding' in names:
        return 'researcher'
    if names & {'submit_contract_review', 'submit_contract_oracle'}:
        return 'reviewer'
    if any(name.startswith('submit_') for name in names):
        return 'verifier'
    return 'worker'


def claims_root():
    path = Path.home() / '.hornet' / 'claims'
    if any(is_link(part) for part in (path, *path.parents)):
        raise HornetError('Hive claim directory must not use linked paths')
    return path


def claim_path(key):
    path = claims_root() / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
    if is_link(path) or is_link(Path(str(path) + '.guard')):
        raise HornetError('Linked hive claim refused')
    return path


@contextmanager
def worker_claim(settings, worker, run=None):
    binding = getattr(worker, 'connection_binding', None)
    if not settings.get('hive') or binding is None:
        yield None
        return
    if not isinstance(binding, str) or not re.fullmatch(r'[0-9a-f]{64}', binding):
        raise HornetError('Invalid pinned worker identity for hive reservation')
    with WorkerLock(claim_path('worker:' + binding), run) as lock:
        yield lock


@contextmanager
def claim(profile, run):
    if profile is None:
        yield
        return
    keys = {'endpoint:' + endpoint(profile)}
    if profile.get('resource'):
        keys.add('resource:' + profile['resource'])
    with ExitStack() as stack:
        for key in sorted(keys):
            path = claim_path(key)
            try:
                stack.enter_context(WorkerLock(path, run))
            except HornetError as exc:
                raise AgentBusy("Hive agent '{}' cannot claim its endpoint/resource: {}. Resume the saved run when available.".format(profile['agent'], exc)) from None
        yield


def listing(project):
    value = registry(project)
    return {'ok': True, 'path': str(project.path('hive.json')), 'default_agent': value['default_agent'],
            'agents': [{'label': name, **profile} for name, profile in value['agents'].items()],
            'roles': value['roles'], 'cells': value['cells']}


def assign(project, run_name, cell, agent):
    from .coding import autonomous, tree
    run = project.path(run_name, write=True)
    with WorkerLock(run / 'hive-assignments.lock', run):
        state = autonomous.load(project, run)
        if not state['settings'].get('hive'):
            raise HornetError('This goal was not started with a hive configuration')
        value = validate_binding(state['settings']['hive'])
        if label(agent) not in value['agents']:
            raise HornetError('Unknown agent in this goal\'s saved hive: ' + agent)
        if cell not in {item['id'] for item in tree.all_cells(state)}:
            raise HornetError('Select an exact cell ID from this goal')
        if state['phase'] == 'complete' or cell in state['values']:
            raise HornetError('Completed cells retain their assignment; create a revision for new work')
        updated = dict(assignments(run, value), **{cell: agent})
        receipt = {'cell': cell, 'agent': agent, 'applies': 'next model request for this implementation cell'}
        write_text(run / 'hive-assignment-history' / (uuid.uuid4().hex + '.json'), json.dumps(receipt, indent=2))
        write_text(run / 'hive-assignments.json', json.dumps({'schema_version': 1, 'cells': updated}, indent=2), force=True)
        return dict(receipt, ok=True, run_dir=str(run))

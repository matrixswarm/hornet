"""Cell registry: reviewed transformations prepare ordinary durable worker writes.

Native assets see a disposable candidate only. They never receive a guest path
or execute generated code. Scope and source versions are checked before the
candidate is handed to the existing worker/journal. No remote plugin install.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile

from hornet.project import HornetError, write_text
from hornet.registry.registry_manager import RegistryManager
from hornet.registry.proxy_resolver import ProxyResolver, decode_arguments
from hornet.registry.registry_mcp_server import TOOLS as GATEWAY
from hornet.registry.schema_validation import validate
from . import cells, read_refs

ROOT = Path(__file__).resolve().parents[1] / 'registry'
# Adding a native asset does not grant it cell access. Review its effects and
# declare its sole file argument here (None means a pure value transformation).
ADAPTERS = {
    'data.json.wrap': None, 'files.text.indent': None,
    'files.text.read': 'path', 'files.text.atomic_edit': 'path',
    'data.json.query': 'file_path', 'data.json.edit': 'file_path',
    'data.json.batch': 'file_path', 'data.json.validate_shape': 'file_path',
    'data.json.move': 'file_path', 'data.json.ordered_edit': 'path',
    'languages.coding.python.interface.create': 'path',
    'languages.coding.python.design_pattern.strategy': 'path',
    'languages.coding.python.method.fill': 'path',
    'languages.coding.python.imports.add': 'path',
    'languages.coding.python.interface.stage': 'path',
    'languages.coding.python.design_pattern.stage': 'path',
    'languages.coding.python.method.stage': 'path',
}
RECIPES = {'recipe.atomic_commit', 'recipe.precision_formatting'}
NAMES = {t['name'] for t in GATEWAY}
TOOLS = [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'],
         'parameters': copy.deepcopy(t['inputSchema'])}} for t in GATEWAY]
for item in TOOLS:
    if item['function']['name'] == 'call_proxy_tool':
        item['function']['parameters']['properties']['arguments']['anyOf'][0].update(
            properties={}, additionalProperties=True)

GUIDE = """Registry tools are available for this cell. find_asset searches only reviewed cell assets;
search for an operation such as 'method' or 'read', not project class/function names.
For example, changing Calculator.double needs a method-body operation; Calculator is not
a catalog asset. If a search has no matches, try an operation word such as method,
read or json; an unrelated operation is not a match.
get_asset_details returns one exact contract. Read it before call_proxy_tool. Canonical URIs
are offered for execution only after inspection; the two most recently inspected tool
contracts can appear in call_proxy_tool. Edits of existing files also need a current read;
read_file supplies the initial version. Successful commits include a separately verified
readback and its current hash, which can be used for the next edit. Inspect a tool again
to bring its contract back.
Provide the arguments object shown there, without inventing extra fields. Canonical URIs
are identifiers, never filesystem/import paths. Paths are relative to the assigned worker
workspace and must belong to this cell. Read before editing. Prefer read_file and pass
its short read_ref to a mutating operation; the controller resolves the observed hash.
Alternatively, copy source_sha256 from a registry read into expected_sha256. Never supply
both version arguments. Set commit explicitly. STAGED is a
preview retained in this job's registry receipt, not a finished edit. Registry writes use
the same durable worker journal as write_file. Inspect the returned readback, then check_ticket.
The controller will not accept an unverified completion. Shell/probe and arbitrary plugins
are unavailable through the cell registry. Errors are evidence: correct the arguments or
choose another allowed tool; unchanged failures have a bounded retry budget."""


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def manager(allowed=None):
    names = set(ADAPTERS) | RECIPES if allowed is None else set(allowed)
    if not names <= set(ADAPTERS) | RECIPES:
        raise ValueError('Registry cell scope includes an asset without a reviewed cell adapter')
    return RegistryManager(root=ROOT, catalog_path=ROOT / 'asset_catalog.json', allowed_uris=names)


def binding(worker):
    allowed = getattr(worker, 'allowed', None)
    if not allowed:
        raise HornetError('Registry tools require an explicit cell file scope')
    files = sorted(cells.path(p) for p in allowed)
    assets = sorted(getattr(worker, 'registry_assets', set(ADAPTERS) | RECIPES))
    catalog = manager(assets)
    sources = sorted([*ROOT.glob('*.py'), *ROOT.glob('*.json'), *ROOT.glob('assets/*.py'), *ROOT.glob('recipes/*.json'), Path(__file__)])
    fingerprint = digest({p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else 'cell-adapter.py':
                          hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    return {'version': 1, 'files': files, 'assets': sorted(catalog.assets), 'implementation_sha256': fingerprint}


def check_binding(value, worker=None):
    if not isinstance(value, dict) or set(value) != {'version', 'files', 'assets', 'implementation_sha256'} or value['version'] != 1:
        raise HornetError('Invalid saved cell registry binding')
    class Scope:
        allowed = value['files']
        registry_assets = value['assets']
    if binding(worker or Scope()) != value:
        raise HornetError('Registry implementation or cell permissions changed; preserve this run and start a reviewed revision')


def details(catalog, uri):
    value = catalog.details(uri)
    value.pop('resolved_path', None)
    value.pop('path', None)
    if value['type'] == 'TOOL':
        field = ADAPTERS[value['uri']]
        value['cell_access'] = 'write' if value.get('mutates') else 'read' if field else 'value'
        if field:
            value = copy.deepcopy(value)
            value['inputSchema']['properties'][field]['description'] = 'Exact relative path in this cell file scope.'
            for example in value.get('examples', []):
                if field in example:
                    example[field] = Path(example[field]).name
            if value.get('mutates'):
                value['inputSchema']['properties']['read_ref'] = {
                    'type': 'string', 'minLength': 1,
                    'description': 'Short read_ref from read_file in this job for this exact file version. Prefer this to copying a hash.'}
                value['inputSchema']['oneOf'] = [
                    {'required': ['read_ref']}, {'required': ['expected_sha256']}]
        value['notes'] = value.get('notes', '') + ' Cell previews are retained in the job receipt; no shadow_path is published. Read back after commit.'
    return value


def execution_schema(asset, job):
    """Offer only observed current versions, without granting a newer version.

    The menu is regenerated from durable reads. A completed write invalidates its
    old menu until another read; recovery reconstructs the same choices.
    """
    field = ADAPTERS[asset['uri']]
    schema = asset['inputSchema']
    if not (field and asset.get('mutates')):
        return schema
    versions = {}
    for ref, observed in read_refs.observations(job).items():
        path = observed['path']
        if job['snapshot']['files'].get(path) == observed['sha256']:
            versions[path] = ('read_ref', ref)
    for entry in reversed(job['history']):
        result = entry.get('result', {})
        path = result.get('path')
        observed = result.get('readback_sha256', result.get('source_sha256'))
        if (path and path not in versions and result.get('ok') and result.get('readback')
                and observed == job['snapshot']['files'].get(path) and observed):
            versions[path] = ('expected_sha256', observed)
    variants = []
    for path in job['registry_binding']['files']:
        version = versions.get(path)
        if path not in job['snapshot']['files']:
            version = ('expected_sha256', '')
        if version is None:
            continue
        key, value = version
        choice = copy.deepcopy(schema)
        choice.pop('oneOf', None)
        choice['properties'].pop('expected_sha256' if key == 'read_ref' else 'read_ref', None)
        choice['properties'][field]['enum'] = [path]
        choice['properties'][key] = {'type': 'string', 'enum': [value]}
        choice['required'] = sorted(set(choice.get('required', [])) | {field, key})
        variants.append(choice)
    return {'anyOf': variants} if variants else None


def offered_tools(selected, job):
    """Bound the execution menu to contracts the agent has actually inspected.

    This is a model-facing aid, not an authorization shortcut. Preparation still
    checks the frozen catalog, inspection journal, scope and current source hash.
    The menu can be rebuilt after a crash from the same validated observations.
    """
    catalog = manager(job['registry_binding']['assets'])
    recent = []
    for entry in reversed(job['history']):
        if entry.get('tool') != 'get_asset_details' or not entry.get('result', {}).get('ok'):
            continue
        uri = entry['result']['asset']['uri']
        if uri not in ADAPTERS or uri in recent:
            continue
        recent.append(uri)
        if len(recent) == 2:
            break
    offered = []
    for original in selected:
        item = copy.deepcopy(original)
        function = item['function']
        if function['name'] == 'call_proxy_tool':
            if not recent:
                continue
            variants = []
            for uri in recent:
                asset = details(catalog, uri)
                schema = execution_schema(asset, job)
                if schema is None:
                    continue
                variants.append({'type': 'object', 'properties': {
                    'uri': {'type': 'string', 'enum': [uri]},
                    'arguments': schema},
                    'required': ['uri', 'arguments'], 'additionalProperties': False})
            if not variants:
                continue
            function['parameters'] = {'anyOf': variants}
            function['description'] = ('Execute one of the inspected contracts below. '
                'Use get_asset_details to inspect a different operation. '
                'Provide its exact arguments object; check the returned status.')
        elif function['name'] == 'read_file' and job['settings'].get('registry_only'):
            function['description'] = ('Read numbered file lines, SHA-256 and a short read_ref; default 80 lines. '
                'For registry edits, pass read_ref to the inspected operation. '
                'Confirmed registry writes include readback and the new hash. Read again after a stale-source error.')
        offered.append(item)
    return offered


def prepare(worker, name, arguments, policy, snapshot, inspected, job=None):
    """Return a bounded result and optional deterministic write_file operation."""
    check_binding(policy, worker)
    catalog = manager(policy['assets'])
    gateway = next(t for t in GATEWAY if t['name'] == name)
    validate(arguments, gateway['inputSchema'])
    result, operation = {'ok': True, 'status': 'SUCCESS'}, None
    if name == 'find_asset':
        result.update(assets=catalog.find(arguments['query'], min(arguments.get('limit', 10), 20)), next='get_asset_details(uri)')
        if not result['assets']:
            result.update(hint='No matching capability. The registry indexes reusable operations, '
                          'not your project classes or functions. Search for an operation such as '
                          'method, read, or json, then inspect a matching URI with get_asset_details. '
                          'Do not choose an unrelated editor just because its arguments accept a path.')
    elif name == 'get_asset_details':
        result['asset'] = details(catalog, arguments['uri'])
    elif name == 'read_recipe':
        asset = catalog.details(arguments['uri'])
        if asset['type'] != 'RECIPE':
            raise ValueError('Use read_recipe only for recipe assets')
        result.update(uri=asset['uri'], content=Path(asset['resolved_path']).read_text(encoding='utf-8'))
    elif name == 'registry_status':
        result.update(files=policy['files'], assets=policy['assets'], implementation_sha256=policy['implementation_sha256'])
    else:
        asset = catalog.details(arguments['uri'])
        uri = asset['uri']
        if uri not in ADAPTERS or uri not in inspected:
            raise ValueError('Inspect get_asset_details for an allowed tool before calling it')
        inner = decode_arguments(arguments['arguments'])
        field = ADAPTERS[uri]
        if 'read_ref' in inner and asset.get('mutates') and field:
            if job is None:
                raise ValueError('A read_ref requires this cell\'s confirmed read history')
            version = {'path': inner.get(field), 'read_ref': inner['read_ref']}
            if 'expected_sha256' in inner:
                version['expected_sha256'] = inner['expected_sha256']
            resolved = read_refs.resolve(job, version)
            inner = dict(inner, expected_sha256=resolved['expected_sha256'])
            del inner['read_ref']
        validate(inner, asset['inputSchema'])
        path = cells.path(inner[field]) if field else None
        if path is not None and path not in policy['files']:
            raise ValueError('Registry path is outside this cell file scope: ' + path)
        before = snapshot['files'].get(path) if path else None
        mutates = asset.get('mutates', False)
        commit = inner.get('commit', not uri.endswith('.stage'))
        if mutates and before is not None and inner.get('expected_sha256') != before:
            raise ValueError('Read the current file again; pass its read_ref or source_sha256 as expected_sha256')
        original = None
        if before is not None:
            observed = worker.call('read_source', {'path': path})
            if not observed.get('ok'):
                raise ValueError(observed.get('error', 'Cannot read the worker source'))
            original = base64.b64decode(observed['content_base64'], validate=True)
            if len(original) > 256 * 1024 or hashlib.sha256(original).hexdigest() != before:
                raise ValueError('Source changed during registry preparation; nothing was written')
        with tempfile.TemporaryDirectory(prefix='hornet-registry-candidate-') as directory:
            candidate = Path(directory) / (Path(path).name if path else 'value')
            if original is not None:
                candidate.write_bytes(original)
            local = dict(inner)
            if field:
                local[field] = str(candidate)
            # Commit only to the disposable candidate. The real write belongs
            # to Hornet's existing operation journal and scope-checked worker.
            if mutates:
                local['commit'] = True
            response = ProxyResolver(registry=catalog).execute(uri, local)
            if response['status'] == 'ERROR':
                raise ValueError(response['proxy_response'].get('message', 'Asset failed').replace(str(candidate), path or '<candidate>'))
            output = response['proxy_response']
            output.pop('shadow_path', None)
            if 'path' in output:
                output['path'] = path
            result.update(uri=uri, path=path, proxy_response=output)
            if mutates:
                content = candidate.read_bytes()
                if len(content) > 256 * 1024:
                    raise ValueError('Candidate exceeds the worker file limit')
                result.update(status='SUCCESS' if commit else 'STAGED', sha256=hashlib.sha256(content).hexdigest(),
                              source_sha256=before, changed=original != content)
                output['status'] = result['status']
                if commit:
                    operation = {'path': path, 'content': content.decode('utf-8'), 'expected_sha256': before or ''}
                else:
                    result['preview'] = content.decode('utf-8')
                    if len(encoded(result).encode()) > 65536:
                        raise ValueError('Cell preview exceeds 64 KiB; request a smaller change')
            elif path:
                result['source_sha256'] = before
                result['readback'] = uri in {'files.text.read', 'data.json.query'}
    if len(encoded(result).encode()) > 65536:
        raise ValueError('Registry result exceeds 64 KiB; narrow the requested range')
    return result, operation


def record(run, job, call, result, operation):
    value = {'version': 1, 'call': call, 'policy': job['registry_binding'], 'result': result, 'operation': operation}
    relative = 'registry/' + hashlib.sha256(call['id'].encode()).hexdigest() + '.json'
    path = run / relative
    path.parent.mkdir(exist_ok=True)
    text = encoded(value)
    if path.exists():
        if path.read_text(encoding='utf-8') != text:
            raise HornetError('Registry preparation identity changed')
    else:
        write_text(path, text)
    return {'path': relative, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


def read_record(run, ref, job, call=None):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise HornetError('Invalid registry receipt reference')
    import re
    if not re.fullmatch(r'registry/[0-9a-f]{64}\.json', ref['path']):
        raise HornetError('Invalid registry receipt path')
    path = run / ref['path']
    if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 1500 * 1024:
        raise HornetError('Invalid registry receipt file')
    raw = path.read_bytes()
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'version', 'call', 'policy', 'result', 'operation'}
            or value['version'] != 1 or hashlib.sha256(raw).hexdigest() != ref['sha256']
            or value['policy'] != job['registry_binding'] or (call is not None and value['call'] != call)):
        raise HornetError('Registry receipt changed or belongs to another call')
    if ref['path'] != 'registry/' + hashlib.sha256(value['call']['id'].encode()).hexdigest() + '.json':
        raise HornetError('Registry receipt filename does not match its call')
    return value


def _readback_result(value, ref):
    raw = base64.b64decode(value['content_base64'], validate=True)
    if len(raw) > 256 * 1024 or hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise HornetError('Committed-file readback bytes do not match their hash')
    lines = raw.decode('utf-8').splitlines()
    preview = '\n'.join('%s: %s' % (i + 1, line) for i, line in enumerate(lines[:80]))
    return {'readback': True, 'readback_sha256': value['sha256'], 'readback_receipt': ref,
            'readback_content': preview[:6000], 'readback_truncated': len(lines) > 80 or len(preview) > 6000,
            'readback_origin': 'controller_after_confirmed_commit'}


def readback_record(run, ref, job, call_id, prepared):
    relative = 'registry-readbacks/' + hashlib.sha256(call_id.encode()).hexdigest() + '.json'
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'} or ref['path'] != relative:
        raise HornetError('Invalid committed-file readback reference')
    path = run / relative
    if path.is_symlink() or path.parent.is_symlink() or path.stat().st_size > 512 * 1024:
        raise HornetError('Invalid committed-file readback artifact')
    raw = path.read_bytes()
    value = json.loads(raw)
    if (hashlib.sha256(raw).hexdigest() != ref['sha256'] or not isinstance(value, dict)
            or set(value) != {'version', 'job_id', 'binding', 'call_id', 'path', 'sha256', 'content_base64'}
            or value['version'] != 1 or value['job_id'] != job['job_id']
            or value['binding'] != job['snapshot']['binding'] or value['call_id'] != call_id
            or value['path'] != prepared['operation']['path'] or value['sha256'] != prepared['result']['sha256']):
        raise HornetError('Committed-file readback binding changed')
    return _readback_result(value, ref)


def confirm_commit(run, job, call, prepared, worker, current, take_snapshot):
    """Observe committed bytes independently of the write acknowledgement.

    Repeating this read after a crash is safe; the write still uses its original
    durable operation ID. No extra model turn or permission is required.
    """
    path, expected = prepared['operation']['path'], prepared['result']['sha256']
    if current['files'].get(path) != expected:
        raise HornetError('Confirmed registry write does not match the current source snapshot')
    reply = worker.call('read_source', {'path': path})
    if reply.get('ok') is not True:
        raise HornetError('Registry write confirmed but readback failed: ' + str(reply.get('error', 'unavailable')))
    value = {'version': 1, 'job_id': job['job_id'], 'binding': current['binding'],
             'call_id': call['id'], 'path': path, 'sha256': expected, 'content_base64': reply['content_base64']}
    _readback_result(value, None)  # Validate bytes before recording evidence.
    if take_snapshot(worker) != current:
        raise HornetError('Worker identity or source changed during committed-file readback')
    relative = 'registry-readbacks/' + hashlib.sha256(call['id'].encode()).hexdigest() + '.json'
    target = run / relative
    target.parent.mkdir(exist_ok=True)
    text = encoded(value)
    if target.exists():
        if target.read_text(encoding='utf-8') != text:
            raise HornetError('Committed-file readback evidence changed')
    else:
        write_text(target, text)
    return readback_record(run, {'path': relative, 'sha256': hashlib.sha256(text.encode()).hexdigest()},
                           job, call['id'], prepared)


def validate_history(run, job):
    from .agent import validate_call
    for entry in job['history']:
        if entry.get('tool') not in NAMES:
            if 'registry_receipt' in entry.get('result', {}):
                raise HornetError('Registry receipt attached to a different tool')
            continue
        ref = entry['result']['registry_receipt']
        prepared = read_record(run, ref, job)
        name, arguments = validate_call(prepared['call'])
        if (prepared['call']['id'] != entry['call_id'] or name != entry['tool']
                or arguments != entry['arguments']):
            raise HornetError('Registry history identity changed')
        if prepared['operation'] is not None:
            if (entry.get('dispatched_tool') != 'write_file' or not entry.get('operation_id')
                    or entry.get('dispatched_arguments') != prepared['operation']):
                raise HornetError('Registry write lost its durable operation evidence')
            readback = None
            if entry['operation_result'].get('ok'):
                readback = readback_record(run, entry['result'].get('readback_receipt'),
                                          job, entry['call_id'], prepared)
            expected = finish(prepared, entry['operation_result'], readback)
        else:
            if any(k in entry for k in ('operation_id', 'dispatched_tool', 'operation_result', 'dispatched_arguments')):
                raise HornetError('Read-only registry result contains write evidence')
            expected = prepared['result']
        if entry['result'] != dict(expected, registry_receipt=ref):
            raise HornetError('Registry result differs from its retained preparation and worker receipt')


def failure_signature(name, arguments, snapshot, job=None):
    normalized = dict(arguments)
    if name == 'call_proxy_tool' and 'arguments' in normalized:
        try:
            inner = dict(decode_arguments(normalized['arguments']))
            # A new handle for the same observed bytes is not a new attempt
            # circumstance. Keep malformed/stale references distinct from a
            # corrected reference; never substitute the current source hash.
            field = ADAPTERS.get(normalized.get('uri'))
            if job is not None and field and 'read_ref' in inner:
                version = {'path': inner.get(field), 'read_ref': inner['read_ref']}
                if 'expected_sha256' in inner:
                    version['expected_sha256'] = inner['expected_sha256']
                try:
                    resolved = read_refs.resolve(job, version)
                except ValueError:
                    pass
                else:
                    del inner['read_ref']
                    inner['expected_sha256'] = resolved['expected_sha256']
            normalized['arguments'] = inner
        except (ValueError, TypeError):
            pass
    # A changed source is a new circumstance. Preserve every failed receipt,
    # but do not poison a later attempt after the agent fixes a prerequisite.
    return digest([name, normalized, snapshot['files']])


def inspected(job):
    return {e['result']['asset']['uri'] for e in job['history']
            if e.get('tool') == 'get_asset_details' and e.get('result', {}).get('ok')}


def pending_readbacks(job):
    changed, observed = {}, {}
    for entry in job['history']:
        result = entry.get('result', {})
        if entry.get('tool') == 'call_proxy_tool' and result.get('ok'):
            if result.get('status') == 'SUCCESS' and result.get('changed'):
                changed[result['path']] = result['sha256']
            if result.get('readback'):
                observed[result['path']] = result.get('readback_sha256', result.get('source_sha256'))
        if entry.get('tool') == 'read_file' and result.get('ok'):
            observed[result['path']] = result['sha256']
    return sorted(p for p in changed if observed.get(p) != job['snapshot']['files'].get(p))


def finish(prepared, result, readback=None):
    if prepared['operation'] is None:
        return prepared['result']
    expected = prepared['result']['sha256']
    if result.get('ok') and result.get('sha256') != expected:
        raise HornetError('Worker registry commit returned an unexpected content hash')
    combined = dict(prepared['result'], ok=result.get('ok') is True, worker_result=result)
    if combined['ok'] and readback is not None:
        combined.update(readback)
    if not combined['ok']:
        combined.update(status='ERROR', error=result.get('error', 'Worker write failed'), changed=False)
    return combined

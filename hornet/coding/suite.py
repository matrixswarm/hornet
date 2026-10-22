"""Run every discovered unittest in bounded batches of whole modules."""
import json
import re
import time


MARKER = 'HORNET_SUITE_RESULT:'
SCRIPT = """import hashlib, json, os, sys, tempfile, unittest
sys.path.insert(0, os.getcwd())
request = json.loads(sys.argv[1])
def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item
with tempfile.TemporaryDirectory(prefix='hornet-suite-cache-') as cache:
    sys.pycache_prefix = cache
    sys.dont_write_bytecode = True
    suite = unittest.defaultTestLoader.discover('tests')
    modules = {}
    for case in flatten(suite):
        modules.setdefault(type(case).__module__, []).append(case)
    batches, current = [], []
    for cases in modules.values():
        if current and len(current) + len(cases) > 50:
            batches.append(current)
            current = []
        current.extend(cases)
    if current or not batches:
        batches.append(current)
    identity = [[case.id() for case in batch] for batch in batches]
    digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    counts = [len(batch) for batch in batches]
    if request['action'] == 'discover':
        result = {'sha256': digest, 'counts': counts}
        code = 0
    else:
        if request['sha256'] != digest:
            raise RuntimeError('Discovered test inventory changed between batches')
        index = request['index']
        outcome = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(batches[index]))
        result = {'sha256': digest, 'index': index, 'expected': counts[index],
                  'ran': outcome.testsRun, 'ok': outcome.wasSuccessful()}
        code = 0 if result['ok'] and result['ran'] == result['expected'] else 1
    print('HORNET_SUITE_RESULT:' + json.dumps(result), flush=True)
raise SystemExit(code)
"""


def receipt(reply):
    lines = reply.get('output', '').splitlines()
    if not lines or not lines[-1].startswith(MARKER):
        raise ValueError('Missing suite completion receipt')
    value = json.loads(lines[-1][len(MARKER):])
    if not isinstance(value, dict):
        raise ValueError('Invalid suite receipt')
    return value


def run_suite(rpc, clock=time.monotonic):
    """Pin discovery, preserve module fixtures, require all batches to finish.

    The worker's 120-second command ceiling is unchanged. A complete suite also
    has a 300-second wall budget and at most 64 batches. No result is cached.
    """
    started = clock()
    records, output, expected, ran = [], '', 0, 0
    digest, complete, code, timed_out = None, False, 1, False

    def dispatch(request):
        remaining = int(300 - (clock() - started))
        if remaining < 1:
            raise TimeoutError('Complete suite exceeded its 300-second budget')
        reply = rpc('run_command', {'argv': ['python3', '-I', '-c', SCRIPT,
                    json.dumps(request)], 'timeout': min(120, remaining)})
        records.append({'request': request, 'result': reply})
        return reply

    try:
        discovery = dispatch({'action': 'discover'})
        output += discovery.get('output', '')
        timed_out |= bool(discovery.get('timed_out'))
        if discovery.get('ok') is not True or discovery.get('exit_code') != 0:
            raise ValueError('Test discovery did not complete')
        inventory = receipt(discovery)
        counts, digest = inventory['counts'], inventory['sha256']
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or not isinstance(counts, list) or not 1 <= len(counts) <= 64
                or any(type(count) is not int or count < 0 for count in counts)
                or any(count == 0 for count in counts) and counts != [0]):
            raise ValueError('Invalid or oversized test inventory')
        expected, passed = sum(counts), True
        for index, count in enumerate(counts):
            reply = dispatch({'action': 'run', 'index': index, 'sha256': digest})
            output += '\n' + reply.get('output', '')
            timed_out |= bool(reply.get('timed_out'))
            result = receipt(reply)
            if (result.get('sha256') != digest or type(result.get('index')) is not int
                    or result['index'] != index or type(result.get('expected')) is not int
                    or result['expected'] != count or type(result.get('ran')) is not int
                    or result['ran'] != count or type(result.get('ok')) is not bool):
                raise ValueError('Suite batch did not verify its full pinned inventory')
            ran += result['ran']
            passed &= result['ok'] and reply.get('ok') is True and reply.get('exit_code') == 0
        if clock() - started > 300:
            raise TimeoutError('Complete suite exceeded its 300-second budget')
        complete = True
        code = 0 if passed else 1
        output += '\nRan {} tests across {} batches\n\n{}\n'.format(ran, len(counts), 'OK' if passed else 'FAILED')
    except (ValueError, KeyError, TypeError, TimeoutError) as exc:
        timed_out |= isinstance(exc, TimeoutError)
        code = 124 if timed_out else 1
        output += '\nSuite verification incomplete: ' + str(exc) + '\n'
    return {'ok': complete and code == 0, 'exit_code': code, 'complete': complete,
            'tests_run': ran, 'expected_tests': expected, 'suite_sha256': digest,
            'output': output[-24000:], 'output_bytes': len(output.encode('utf-8')),
            'truncated': len(output) > 24000, 'timed_out': timed_out,
            'seconds': round(clock() - started, 3), 'batches': records}

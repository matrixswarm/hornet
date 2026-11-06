"""Opt-in live local Gemma trial; not part of unittest discovery.

Run from the checkout with NumPy and a running local LM Studio server:
    python tests/live_gemma_math_eval.py

Only the math registry asset is available to the model. Results and summaries
are printed to stdout; no personal files are read or modified by the test.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hornet.registry.registry_manager import RegistryManager
from hornet.registry.registry_mcp_server import RegistryMCPServer, STANDALONE_TOOLS

MODEL = os.environ.get('HORNET_EVAL_MODEL', 'google/gemma-4-12b')
URL = os.environ.get('HORNET_EVAL_URL', 'http://127.0.0.1:1234/v1/chat/completions')
A = [[1, 2], [3, 4]]
B = [[2, 0], [1, 2]]
MAX_TURNS = 8
DEADLINE_SECONDS = 180


def main():
    parsed = urllib.parse.urlsplit(URL)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Live evaluation only supports a loopback HTTP model endpoint')
    gateway = next((tool for tool in STANDALONE_TOOLS if tool['name'] == 'call_registry_tool'), None)
    if gateway is None:
        raise RuntimeError('Standalone registry does not expose call_registry_tool')
    selector = gateway['inputSchema']['properties']['arguments']
    if 'capability_name' not in selector.get('required', []):
        raise RuntimeError('Standalone gateway schema does not require capability_name')
    model_tool = {'type': 'function', 'function': {'name': gateway['name'],
                  'description': gateway['description'], 'parameters': gateway['inputSchema']}}
    messages = [
        {'role': 'system', 'content': 'Use call_registry_tool to compute the requested matrix product. '
         'Set tool_name to matrix_multiplication. In arguments provide capability_name as '
         'matrix_multiplication and the requested A and B arrays. Inspect the returned result. '
         'Then report the product and independently verify at least one entry by row-column arithmetic.'},
        {'role': 'user', 'content': 'Calculate A @ B for A=[[1,2],[3,4]] and B=[[2,0],[1,2]]. '
         'Use the registry tool and show a separate row-column check.'},
    ]
    # The expected answer is calculated independently of the registry tool.
    expected = [[sum(A[i][k] * B[k][j] for k in range(len(B)))
                 for j in range(len(B[0]))] for i in range(len(A))]
    deadline = time.monotonic() + DEADLINE_SECONDS
    events = []
    final = None
    failure = None
    with tempfile.TemporaryDirectory(prefix='hornet-live-math-') as temp:
        registry = RegistryManager(db_path=Path(temp) / 'index.sqlite',
                                   allowed_uris=['math.linear_algebra.compute'])
        server = RegistryMCPServer(registry)
        for turn in range(MAX_TURNS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            body = {'model': MODEL, 'messages': messages, 'tools': [model_tool],
                    'temperature': 0, 'max_tokens': 1024}
            request = urllib.request.Request(URL, data=json.dumps(body).encode('utf-8'),
                                             headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with urllib.request.urlopen(request, timeout=min(45, remaining)) as response:
                    result = json.load(response)
            except (OSError, ValueError) as exc:
                failure = f'Model request failed: {type(exc).__name__}: {exc}'
                break
            message = result['choices'][0]['message']
            messages.append(message)
            calls = message.get('tool_calls') or []
            if not calls:
                final = message.get('content') or ''
                break
            for call in calls:
                name = call.get('function', {}).get('name')
                raw = call.get('function', {}).get('arguments', '{}')
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                    inner = args.get('arguments', {}) if isinstance(args, dict) else {}
                    if (name != 'call_registry_tool' or args.get('tool_name') != 'matrix_multiplication'
                            or not isinstance(inner, dict)
                            or inner.get('capability_name') != 'matrix_multiplication'
                            or inner.get('A') != A or inner.get('B') != B):
                        raise ValueError('Only the requested matrix multiplication is permitted')
                    request_json = {'jsonrpc': '2.0', 'id': len(events) + 1, 'method': 'tools/call',
                                    'params': {'name': name, 'arguments': args}}
                    reply = server.handle_request(json.dumps(request_json))['result']
                    content = reply['content'][0]['text']
                    payload = json.loads(content)
                    events.append({'name': name, 'arguments': args, 'isError': reply['isError'],
                                   'payload': payload})
                except Exception as exc:
                    content = json.dumps({'status': 'error', 'message': str(exc)})
                    events.append({'name': name, 'isError': True, 'error': str(exc)})
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
                if len(events) >= MAX_TURNS:
                    break
            if len(events) >= MAX_TURNS:
                break
    products = [event.get('payload', {}).get('payload', {}).get('result') for event in events
                if not event['isError']]
    passed = expected in products and bool(final)
    print(json.dumps({'passed': passed, 'model': MODEL, 'expected': expected, 'events': events,
                      'final': final, 'failure': failure,
                      'verification': ('Product matched independently calculated values; final explanation requires human review.'
                                       if expected in products else 'No matching product was observed.')},
                     ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())

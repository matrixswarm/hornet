import json
import os
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hornet.distil.model import CancelledError, ModelClient, ModelError, build_messages, discover_models


def chunk(content=None, finish=None):
    return ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': content},
                                              'finish_reason': finish}]}, ensure_ascii=False) + '\n\n').encode()


@contextmanager
def server(body, status=200, delay=0):
    received = []
    started = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            try:
                if delay:
                    self.wfile.write(chunk('partial'))
                    self.wfile.flush()
                    started.set()
                    time.sleep(delay)
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield 'http://127.0.0.1:' + str(http.server_port), received, started
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


class PromptTests(unittest.TestCase):
    def test_full_history_and_details(self):
        source = [{'role': 'assistant' if i % 2 else 'user', 'content': str(i) + 'x' * 9000,
                   'kind': 'distill' if i == 0 else 'chat'} for i in range(30)]
        for detail in ('Brief', 'Balanced', 'Detailed'):
            result = build_messages(source, 'distill', detail)
            self.assertEqual(result[:-1], [{'role': m['role'], 'content': m['content']} for m in source])
            self.assertIn('Detail level: ' + detail, result[-1]['content'])
            self.assertIn('uncertainties', result[-1]['content'])
        self.assertEqual(len(source), 30)
        self.assertIn('kind', source[0])
        self.assertEqual(build_messages(source), build_messages(source, 'distill')[:-1])

    def test_validation(self):
        for host in ('ftp://localhost', 'http://user:secret@localhost', 'http://localhost?q=secret',
                     'http://localhost/other', 'http://localhost:bad'):
            with self.assertRaises(ValueError):
                ModelClient(host, 'test')
        for suffix in ('', '/', '/v1', '/v1/', '/v1/chat/completions'):
            self.assertEqual(ModelClient('http://localhost:1234' + suffix, 'test').endpoint,
                             'http://localhost:1234/v1/chat/completions')


class TransportTests(unittest.TestCase):
    def test_chunked_done_does_not_wait_for_server_connection_to_close(self):
        release = threading.Event()
        class Chunked(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                for frame in (chunk('ok'), chunk(finish='stop'), b'data: [DONE]\n\n'):
                    self.wfile.write(('%x\r\n' % len(frame)).encode() + frame + b'\r\n')
                    self.wfile.flush()
                release.wait(3)
                try:
                    self.wfile.write(b'0\r\n\r\n'); self.wfile.flush()
                except OSError:
                    pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Chunked)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            host = 'http://127.0.0.1:%d' % http.server_port
            start = time.monotonic()
            self.assertEqual(ModelClient(host, 'test', timeout=5).complete([], threading.Event()), 'ok')
            self.assertLess(time.monotonic() - start, 2)
        finally:
            release.set(); http.shutdown(); http.server_close(); worker.join()

    def test_model_discovery_reads_ids_and_rejects_redirects(self):
        requests_seen = []
        class Models(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests_seen.append((self.path, self.headers.get('Authorization')))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'data': [{'id': 'one'}, {'id': 'two'}, {'id': 'one'}]}).encode())
        http = ThreadingHTTPServer(('127.0.0.1', 0), Models)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            host = 'http://127.0.0.1:%d' % http.server_port
            self.assertEqual(discover_models(host + '/v1', api_key='test-key'), ['one', 'two'])
            self.assertEqual(requests_seen, [('/v1/models', 'Bearer test-key')])
        finally:
            http.shutdown(); http.server_close(); worker.join()

    def test_model_discovery_rejects_redirect_without_following(self):
        paths = []
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                paths.append(self.path)
                self.send_response(302)
                self.send_header('Location', '/destination')
                self.end_headers()
        http = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            host = 'http://127.0.0.1:%d' % http.server_port
            with self.assertRaises(ModelError):
                discover_models(host)
            self.assertEqual(paths, ['/v1/models'])
        finally:
            http.shutdown(); http.server_close(); worker.join()

    def test_redirect_does_not_forward_conversation_or_credentials(self):
        received = []
        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                received.append(True)
                self.send_response(200)
                self.end_headers()
        sink = ThreadingHTTPServer(('127.0.0.1', 0), Sink)
        worker = threading.Thread(target=sink.serve_forever, daemon=True)
        worker.start()
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(307)
                self.send_header('Location', 'http://127.0.0.1:%d/redirected' % sink.server_port)
                self.end_headers()
        redirect = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        redirect_worker = threading.Thread(target=redirect.serve_forever, daemon=True)
        redirect_worker.start()
        try:
            host = 'http://127.0.0.1:%d' % redirect.server_port
            with self.assertRaisesRegex(ModelError, 'redirected'):
                ModelClient(host, 'test', api_key='private').complete(
                    [{'role': 'user', 'content': 'private conversation'}], threading.Event())
            self.assertEqual(received, [])
        finally:
            redirect.shutdown(); redirect.server_close(); redirect_worker.join()
            sink.shutdown(); sink.server_close(); worker.join()

    def test_full_unicode_stream_and_request(self):
        expected = '🐝 café 漢字\n' * 4000
        messages = [{'role': 'user' if i % 2 else 'assistant', 'content': 'x' * 9001}
                    for i in range(20)]
        with server(chunk(expected) + chunk(finish='stop') + b'data: [DONE]\n\n') as (host, seen, _):
            self.assertEqual(ModelClient(host, 'test').complete(messages, threading.Event()), expected)
            self.assertEqual(seen[0]['messages'], messages)
            self.assertNotIn('max_tokens', seen[0])

    def test_output_token_limit_forwarded_to_direct_and_isolated_transport(self):
        body = chunk('ok') + chunk(finish='stop') + b'data: [DONE]\n\n'
        with server(body) as (host, seen, _):
            self.assertEqual(ModelClient(host, 'test', max_tokens=8192).complete([], threading.Event()), 'ok')
            self.assertEqual(ModelClient(host, 'test', max_tokens=12345)._complete_isolated([], threading.Event()), 'ok')
            self.assertEqual([item['max_tokens'] for item in seen], [8192, 12345])
        for value in (0, -1, True, 1000001, '100'):
            with self.assertRaises(ValueError):
                ModelClient('http://localhost', 'test', max_tokens=value)

    def test_rejects_partial_and_malformed(self):
        bodies = [chunk('partial'), chunk('partial') + b'data: [DONE]\n\n',
                  chunk('partial', 'stop'), chunk('partial', 'length') + b'data: [DONE]\n\n',
                  chunk('partial', 'content_filter'), b'data: not-json\n\n',
                  b'data: {"choices":null}\n\n', chunk(finish='stop') + b'data: [DONE]\n\n']
        for body in bodies:
            with self.subTest(body=body), server(body) as (host, _, __):
                with self.assertRaises(ModelError):
                    ModelClient(host, 'test').complete([], threading.Event())

    def test_context_error_does_not_echo_server_secrets(self):
        with server(b'{"error":"context exceeded secret-api-key"}', 400) as (host, _, __):
            with self.assertRaisesRegex(ModelError, 'full chat exceeds') as caught:
                ModelClient(host, 'test', api_key='secret-api-key').complete([], threading.Event())
            self.assertNotIn('secret-api-key', str(caught.exception))

    def test_stop_interrupts_socket(self):
        with server(chunk('finished', 'stop') + b'data: [DONE]\n\n', delay=10) as (host, _, started):
            cancel, errors = threading.Event(), []
            client = ModelClient(host, 'test', timeout=20)
            def run():
                try:
                    client.complete([], cancel)
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            self.assertTrue(started.wait(10))
            # The server may have sent headers before the client has received
            # them, especially on a busy VM. This test exercises cancellation
            # after headers, when the client can interrupt the socket.
            if os.name != 'nt':
                deadline = time.monotonic() + 10
                while client._response is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(client._response)
            cancel.set()
            # UI cancellation and the event watcher can race. Neither may close
            # urllib3 buffers while the request thread is reading them.
            stoppers = [threading.Thread(target=client.cancel) for _ in range(3)]
            for stopper in stoppers:
                stopper.start()
            for stopper in stoppers:
                stopper.join(1)
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CancelledError)

    def test_isolated_transport_completes_and_can_be_cancelled(self):
        body = chunk('hello') + chunk(finish='stop') + b'data: [DONE]\n\n'
        with server(body) as (host, received, __):
            messages = [{'role': 'user', 'content': 'hi', 'local_metadata': object()}]
            self.assertEqual(ModelClient(host, 'test')._complete_isolated(messages, threading.Event()), 'hello')
            self.assertEqual(received[0]['messages'], [{'role': 'user', 'content': 'hi'}])
        with server(body, delay=10) as (host, _, started):
            cancel, errors = threading.Event(), []
            client = ModelClient(host, 'test')
            def run():
                try:
                    client._complete_isolated([], cancel)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(started.wait(10))
            cancel.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CancelledError)

    def test_isolated_transport_can_stop_before_headers(self):
        started, release = threading.Event(), threading.Event()
        class DelayedHeaders(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                started.set()
                release.wait(10)
        http = ThreadingHTTPServer(('127.0.0.1', 0), DelayedHeaders)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            cancel, errors = threading.Event(), []
            client = ModelClient('http://127.0.0.1:%d' % http.server_port, 'test')
            def run():
                try:
                    client._complete_isolated([], cancel)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(started.wait(10))
            cancel.set()
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CancelledError)
        finally:
            release.set()
            http.shutdown()
            http.server_close()

    def test_cancel_before_request(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            ModelClient('http://localhost:1', 'test').complete([], cancel)


if __name__ == '__main__':
    unittest.main()

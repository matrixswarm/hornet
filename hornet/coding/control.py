"""Durable optional operator steering at confirmed action boundaries."""
import json
import hashlib
from pathlib import Path
import time
import uuid

from hornet.project import HornetError, write_text


class StopRequested(HornetError):
    status = 'stopped_by_operator'


def send(project, run, action, message=None):
    directory = project.path(run, write=True)
    if not (directory / 'recipe-checkpoint.json').is_file():
        raise HornetError('Select an autonomous recipe run')
    if json.loads((directory / 'recipe-checkpoint.json').read_text(encoding='utf-8')).get('phase') == 'complete':
        raise HornetError('This recipe has already completed')
    if action not in {'pause', 'continue', 'stop', 'message'}:
        raise HornetError('Unknown recipe control action')
    if action == 'message' and (not isinstance(message, str) or not 1 <= len(message.strip()) <= 2000):
        raise HornetError('Operator message requires 1-2000 characters')
    ident = uuid.uuid4().hex
    item = {'id': ident, 'action': action, 'message': message if action == 'message' else None}
    target = directory / 'control/inbox' / (ident + '.json')
    write_text(target, json.dumps(item, ensure_ascii=False))
    return {'ok': True, 'status': 'queued', 'id': ident, 'action': action,
            'receipt': str(directory / 'control/outbox' / (ident + '.json'))}


class Channel:
    def __init__(self, run, state, save, progress, sleep=time.sleep, clock=time.monotonic):
        self.run, self.state, self.save, self.progress = Path(run), state, save, progress
        self.sleep, self.clock = sleep, clock

    def poll(self):
        inbox = self.run / 'control/inbox'
        legacy = self.run / 'commander_input.txt'
        if legacy.is_file() and not legacy.is_symlink():
            before = legacy.stat()
            if before.st_size <= 12000:
                text = legacy.read_text(encoding='utf-8-sig').strip()
                after = legacy.stat()
                if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and text:
                    ident = hashlib.sha256((str(before.st_mtime_ns) + ':' + text).encode('utf-8')).hexdigest()[:32]
                    if ident not in self.state.setdefault('control_seen', []):
                        action = text.lower() if text.lower() in {'pause', 'continue', 'stop'} else 'message'
                        item = {'id': ident, 'action': action, 'message': text if action == 'message' else None}
                        target = inbox / (ident + '.json')
                        if not target.exists():
                            write_text(target, json.dumps(item, ensure_ascii=False))
                            write_text(self.run / 'control/received' / (ident + '.txt'), text)
        # Timestamp then name gives stable order for messages already queued at a boundary.
        for path in sorted(inbox.glob('*.json'), key=lambda value: (value.stat().st_mtime_ns, value.name)):
            if path.stem in self.state.setdefault('control_seen', []):
                continue
            error = None
            try:
                if path.is_symlink() or path.stat().st_size > 12000:
                    raise ValueError('Oversized or linked control message')
                item = json.loads(path.read_text(encoding='utf-8'))
                if (set(item) != {'id', 'action', 'message'} or item['id'] != path.stem
                        or item['action'] not in {'pause', 'continue', 'stop', 'message'}):
                    raise ValueError('Invalid control envelope')
                if item['action'] == 'message':
                    if not isinstance(item['message'], str) or not 1 <= len(item['message'].strip()) <= 2000:
                        raise ValueError('Invalid message text')
                    if len(self.state.setdefault('steering', [])) >= 32:
                        raise ValueError('Recipe has reached its 32-message limit')
                    self.state['steering'].append({'id': item['id'], 'text': item['message']})
                elif item['action'] == 'stop':
                    self.state['stop_requested'] = True
                else:
                    self.state['paused'] = item['action'] == 'pause'
            except (OSError, ValueError, TypeError, KeyError) as exc:
                error = str(exc)
            self.state['control_seen'].append(path.stem)
            self.save()
            receipt = {'id': path.stem, 'status': 'rejected' if error else 'received_at_boundary', 'error': error}
            write_text(self.run / 'control/outbox' / path.name, json.dumps(receipt), force=True)
            write_text(self.run / 'commander_response.txt', json.dumps(receipt), force=True)

    def boundary(self, job=None):
        started = self.clock()
        self.poll()
        announced = False
        while self.state.get('paused') and not self.state.get('stop_requested'):
            if not announced:
                self.progress('paused', message='Waiting at a confirmed boundary; use control --continue')
                announced = True
            self.sleep(0.25)
            self.poll()
        if self.state.get('stop_requested'):
            raise StopRequested('Recipe stopped by operator at a confirmed boundary')
        if job is not None:
            existing = {item['id'] for item in job.get('steering', [])}
            new = [item for item in self.state.get('steering', []) if item['id'] not in existing]
            if new:
                job.setdefault('steering', []).extend(new)
                job['history'].append({'operator_messages': new})
                job['needs_resume_context'] = True
                job['inbox_updated'] = True
        return self.clock() - started if announced else 0


class PeerInbox:
    """Controller-assigned senders; peers cannot post as the operator."""
    def __init__(self, run, state, save):
        self.run, self.state, self.save = Path(run), state, save

    def recipients(self):
        from . import tree
        ids = [cell['id'] for cell in tree.all_cells(self.state)]
        return ((['intake'] if self.state.get('schema_version', 1) >= 4 else ['planner']) + ids
                + ['verifier-' + ident for ident in ids]
                + (['recon-' + ident for ident in ids] if tree.enabled(self.state) else []))

    def post(self, sender, arguments, call_id):
        if (not isinstance(arguments, dict) or set(arguments) != {'to', 'message'}
                or arguments['to'] not in self.recipients() or sender not in self.recipients()
                or not isinstance(arguments['message'], str) or not 1 <= len(arguments['message'].strip()) <= 1600):
            return {'ok': False, 'error': 'Use a known peer recipient and a message of 1-1600 characters',
                    'recipients': self.recipients()}
        ledger = self.state.setdefault('peer_messages', [])
        existing = next((item for item in ledger if item['sender'] == sender and item['call_id'] == call_id), None)
        if existing:
            if existing['recipient'] != arguments['to'] or existing['message'] != arguments['message']:
                return {'ok': False, 'error': 'A peer call ID cannot be reused for a different message'}
            return {'ok': True, 'status': 'already_queued', 'id': existing['id']}
        if len(ledger) >= 64:
            return {'ok': False, 'error': 'Recipe reached its 64 peer-message limit'}
        item = {'id': uuid.uuid4().hex, 'call_id': call_id, 'sender': sender,
                'recipient': arguments['to'], 'message': arguments['message']}
        ledger.append(item)
        self.save()
        write_text(self.run / 'peers/inbox' / item['recipient'] / (item['id'] + '.json'), json.dumps(item, ensure_ascii=False))
        return {'ok': True, 'status': 'queued', 'id': item['id'], 'recipient': item['recipient']}

    def receive(self, recipient, seen=None):
        received = self.state.setdefault('peer_delivered', {}).setdefault(recipient, [])
        confirmed = received if seen is None else seen
        pending = [item for item in self.state.get('peer_messages', []) if item['recipient'] == recipient and item['id'] not in confirmed]
        for item in pending:
            if item['id'] not in received:
                received.append(item['id'])
            write_text(self.run / 'peers/receipts' / (item['id'] + '.json'),
                json.dumps({'id': item['id'], 'recipient': recipient, 'status': 'delivered_at_boundary'}), force=True)
        if pending:
            self.save()
        return pending

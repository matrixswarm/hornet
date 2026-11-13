"""Isolated, local-first revision storage. SQLite is the recovery authority.

A revision owns one derived UTF-8 artifact, distilled.md. Events are append-only;
replacing the active chat never removes its individual messages from history.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from contextlib import contextmanager, suppress
from uuid import uuid4


class VersionConflict(RuntimeError):
    """The revision changed since the caller loaded it."""


class DistilStore:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else os.environ.get(
            'HORNET_DISTIL_ROOT', str(Path.home() / 'hornet_distil'))).expanduser().absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        db_path = self.root / 'distil.sqlite3'
        if db_path.is_symlink():
            raise ValueError('The database must not be a symlink')
        self._db = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT 'Balanced', draft TEXT NOT NULL DEFAULT '',
                messages TEXT NOT NULL DEFAULT '[]', distilled TEXT);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id TEXT NOT NULL REFERENCES revisions(id),
                type TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        ''')
        with self._transaction():
            columns = {row['name'] for row in self._db.execute('PRAGMA table_info(revisions)')}
            if 'deleted' not in columns:
                self._db.execute('ALTER TABLE revisions ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
        try:
            # Serialize recovery with writers in every other process.
            with self._transaction():
                for row in self._db.execute('SELECT id, distilled FROM revisions'):
                    path = self._path(row['id'])
                    if row['distilled'] is not None:
                        expected = row['distilled'].encode('utf-8')
                        if not path.exists() or path.read_bytes() != expected:
                            staged = self._stage(path, row['distilled'])
                            try:
                                os.replace(staged, path)
                            finally:
                                staged.unlink(missing_ok=True)
                    elif path.exists():
                        # A crash before a first distillation committed.
                        path.unlink()
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
            raise ValueError('Invalid identifier')
        return value

    @staticmethod
    def _text(value):
        if not isinstance(value, str):
            raise TypeError('Expected text')
        return value

    def _path(self, revision_id):
        rid = self._identifier(revision_id)
        directory = self.root / 'revisions'
        for part in (directory, directory / rid, directory / rid / 'distilled.md'):
            if part.is_symlink():
                raise ValueError('Revision paths must not be symlinks')
        return directory / rid / 'distilled.md'

    def distilled_path(self, revision_id):
        self.get_revision(revision_id)
        return self._path(revision_id)

    @staticmethod
    def _stage(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.distil-', dir=str(path.parent))
        staged = Path(name)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content.encode('utf-8'))
                handle.flush()
                os.fsync(handle.fileno())
            return staged
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

    def _event(self, revision_id, event_type, payload):
        self._db.execute('INSERT INTO events(revision_id,type,payload) VALUES(?,?,?)',
                         (revision_id, event_type, json.dumps(payload, ensure_ascii=False)))

    def list_projects(self):
        return [dict(row) for row in self._db.execute('SELECT id,name FROM projects ORDER BY rowid')]

    def create_project(self, name):
        self._text(name)
        if not name.strip():
            raise ValueError('A project needs a name')
        result = {'id': uuid4().hex, 'name': name}
        with self._transaction():
            self._db.execute('INSERT INTO projects(id,name) VALUES(:id,:name)', result)
        return result

    def list_revisions(self, project_id, include_deleted=False):
        self._identifier(project_id)
        query = 'SELECT id,name,deleted FROM revisions WHERE project_id=?'
        if not include_deleted:
            query += ' AND deleted=0'
        return [dict(row) for row in self._db.execute(query + ' ORDER BY rowid', (project_id,))]

    def create_revision(self, project_id, name):
        self._identifier(project_id)
        self._text(name)
        if not name.strip():
            raise ValueError('A revision needs a name')
        result = {'id': uuid4().hex, 'name': name}
        with self._transaction():
            self._db.execute('INSERT INTO revisions(id,project_id,name) VALUES(?,?,?)',
                             (result['id'], project_id, name))
            self._event(result['id'], 'created', {'name': name})
        return result

    def rename_revision(self, revision_id, name, expected_version=None):
        self._text(name)
        if not name.strip():
            raise ValueError('A revision needs a name')
        with self._transaction():
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            self._db.execute('UPDATE revisions SET name=?,version=version+1 WHERE id=?',
                             (name, revision_id))
            self._event(revision_id, 'renamed', {'name': name})
            return self.get_revision(revision_id)

    def delete_revision(self, revision_id, expected_version=None):
        with self._transaction():
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            if not state['deleted']:
                self._db.execute('UPDATE revisions SET deleted=1,version=version+1 WHERE id=?',
                                 (revision_id,))
                self._event(revision_id, 'deleted', {})
            return self.get_revision(revision_id)

    def restore_revision(self, revision_id, expected_version=None):
        with self._transaction():
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            if state['deleted']:
                self._db.execute('UPDATE revisions SET deleted=0,version=version+1 WHERE id=?',
                                 (revision_id,))
                self._event(revision_id, 'restored', {})
            return self.get_revision(revision_id)

    def clone_revision(self, revision_id, name, expected_version=None):
        self._text(name)
        if not name.strip():
            raise ValueError('A revision needs a name')
        new_id = uuid4().hex
        staged = path = None
        installed = False
        self._db.execute('BEGIN IMMEDIATE')
        try:
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            self._db.execute("""INSERT INTO revisions
                (id,project_id,name,detail,draft,messages,distilled) VALUES(?,?,?,?,?,?,?)""",
                (new_id, state['project_id'], name, state['detail'], state['draft'],
                 json.dumps(state['messages'], ensure_ascii=False), state['distilled']))
            self._db.execute("""INSERT INTO events(revision_id,type,payload,created_at)
                SELECT ?,type,payload,created_at FROM events WHERE revision_id=? ORDER BY id""",
                (new_id, revision_id))
            self._event(new_id, 'cloned', {'source_revision_id': revision_id, 'name': name})
            if state['distilled'] is not None:
                path = self._path(new_id)
                if path.parent.exists():
                    raise FileExistsError('Clone destination already exists')
                staged = self._stage(path, state['distilled'])
                os.replace(staged, path)
                installed = True
            result = self.get_revision(new_id)
            self._db.commit()
            return result
        except BaseException:
            try:
                if installed:
                    path.unlink(missing_ok=True)
            finally:
                self._db.rollback()
            raise
        finally:
            if staged is not None:
                with suppress(OSError):
                    staged.unlink(missing_ok=True)

    def get_revision(self, revision_id):
        self._identifier(revision_id)
        row = self._db.execute('SELECT * FROM revisions WHERE id=?', (revision_id,)).fetchone()
        if row is None:
            raise KeyError(revision_id)
        result = dict(row)
        result['messages'] = json.loads(result['messages'])
        return result

    @staticmethod
    def _check_version(state, expected_version):
        if expected_version is not None and state['version'] != expected_version:
            raise VersionConflict('This revision changed. Reload it before saving.')

    def _save_setting(self, revision_id, field, value, expected_version=None):
        self._text(value)
        with self._transaction():
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            self._db.execute('UPDATE revisions SET ' + field + '=?, version=version+1 WHERE id=?',
                             (value, revision_id))
            return self.get_revision(revision_id)

    def save_draft(self, revision_id, text, expected_version=None):
        return self._save_setting(revision_id, 'draft', text, expected_version)

    @staticmethod
    def _detail(detail):
        if detail not in ('Brief', 'Balanced', 'Detailed'):
            raise ValueError('Detail must be Brief, Balanced, or Detailed')

    def save_detail(self, revision_id, detail, expected_version=None):
        self._detail(detail)
        return self._save_setting(revision_id, 'detail', detail, expected_version)

    def add_message(self, revision_id, role, content, expected_version=None):
        self._text(role)
        if role not in ('user', 'assistant'):
            raise ValueError('Role must be user or assistant')
        self._text(content)
        with self._transaction():
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            message = {'role': role, 'content': content}
            messages = state['messages'] + [message]
            self._db.execute('UPDATE revisions SET messages=?,draft=?,version=version+1 WHERE id=?',
                             (json.dumps(messages, ensure_ascii=False),
                              '' if role == 'user' else state['draft'], revision_id))
            self._event(revision_id, 'message', message)
            return self.get_revision(revision_id)

    def replace_with_distilled(self, revision_id, content, detail='Balanced',
                               source='distill', expected_version=None):
        self._text(content)
        self._detail(detail)
        if not content.strip():
            raise ValueError('Distilled or imported content must not be empty')
        self._text(source)
        staged = backup = path = None
        replaced = False
        self._db.execute('BEGIN IMMEDIATE')
        try:
            state = self.get_revision(revision_id)
            self._check_version(state, expected_version)
            path = self._path(revision_id)
            staged = self._stage(path, content)
            # Prepare rollback BEFORE touching the current artifact.
            if state['distilled'] is not None:
                backup = self._stage(path, state['distilled'])
            messages = [{'role': 'assistant', 'kind': 'distilled', 'content': content}]
            self._event(revision_id, source, {
                'content': content, 'detail': detail, 'previous_messages': state['messages'],
                'previous_draft': state['draft']})
            self._db.execute('''UPDATE revisions SET messages=?,distilled=?,detail=?,
                version=version+1 WHERE id=?''',
                (json.dumps(messages, ensure_ascii=False), content, detail, revision_id))
            os.replace(staged, path)
            replaced = True
            result = self.get_revision(revision_id)
            self._db.commit()
            return result
        except BaseException:
            # Hold the write lock until the previous artifact is restored.
            try:
                if replaced:
                    if backup is not None:
                        os.replace(backup, path)
                    else:
                        path.unlink(missing_ok=True)
            finally:
                self._db.rollback()
            raise
        finally:
            for temporary in (staged, backup):
                if temporary is not None:
                    # A cleanup problem must not turn a committed save into a
                    # reported failure; SQLite remains the recovery authority.
                    with suppress(OSError):
                        temporary.unlink(missing_ok=True)

    def history(self, revision_id):
        self.get_revision(revision_id)
        events = []
        for row in self._db.execute('SELECT * FROM events WHERE revision_id=? ORDER BY id',
                                    (revision_id,)):
            event = dict(row)
            event['payload'] = json.loads(event['payload'])
            events.append(event)
        return events

    def close(self):
        self._db.close()

"""Conservative default selections for source imports and model context."""
from pathlib import PurePosixPath


def default_excluded(name):
    path = PurePosixPath(name)
    parts = {part.lower() for part in path.parts[:-1]}
    filename = path.name.lower()
    return (bool(parts & {'chats', 'memories', 'attachments', 'backups'})
            or filename.endswith(('.bak', '.bak.py', '.atomic', '.sqlite', '.sqlite3', '.db', '.pem', '.key'))
            or filename in {'credentials.json', 'secrets.json', 'token.json'})

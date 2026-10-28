"""Private, durable first-run state shared by all server workers."""
import fcntl
import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def setup_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / '.setup.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_private(path, value):
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def initialize(directory, configured=False):
    with setup_lock(directory):
        settings = directory / 'admin.json'
        secret = directory / '.session-secret'
        if not secret.exists():
            existing = json.loads(settings.read_text()) if settings.exists() else {}
            write_private(secret, existing.get('session_secret') or secrets.token_hex(32))
        token = directory / '.setup-token'
        if not configured and not settings.exists() and not token.exists():
            write_private(token, secrets.token_urlsafe(32))
        return secret.read_text()


if __name__ == '__main__':
    directory = Path(os.environ.get('DATA_DIR', str(Path(__file__).resolve().parent / 'data')))
    configured = bool(os.environ.get('ADMIN_PASSWORD_HASH'))
    initialize(directory, configured)
    if not configured and not (directory / 'admin.json').exists():
        print('First-time setup: open the site and enter this setup code:', flush=True)
        print((directory / '.setup-token').read_text(), flush=True)

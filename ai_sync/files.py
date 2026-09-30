from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class SyncError(Exception):
    """An actionable failure whose message contains no configuration values."""


def fingerprint(path: Path):
    if path.is_symlink():
        return ('link', os.readlink(path))
    if not path.exists():
        return None
    if path.is_file():
        return ('file', stat.S_IMODE(path.stat().st_mode), hashlib.sha256(path.read_bytes()).hexdigest())
    if path.is_dir():
        return ('dir', tuple((p.name, fingerprint(p)) for p in sorted(path.iterdir())))
    raise SyncError(f'Unsupported filesystem entry: {path}')


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix='.ai-sync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def remove(path: Path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_entry(source: Path, destination: Path):
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


@dataclass
class Operation:
    kind: str
    path: Path
    value: bytes | Path | None


class Plan:
    def __init__(self, data: Path):
        self.data = data
        self.expected = {}
        self.operations: list[Operation] = []

    def watch(self, path: Path):
        self.expected.setdefault(path, fingerprint(path))

    def read(self, path: Path) -> str:
        self.watch(path)
        if path.is_symlink():
            if not path.exists():
                raise SyncError(f'Broken config symlink: {path}')
            self.watch(path.resolve())
        try:
            return path.read_text() if path.exists() else ''
        except UnicodeError:
            raise SyncError(f'Expected UTF-8 text: {path}') from None

    def json(self, path: Path) -> dict:
        raw = self.read(path)
        if path.exists() and not raw.strip():
            raise SyncError(f'Empty JSON file: {path}')
        try:
            value = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            raise SyncError(f'Invalid JSON: {path}') from None
        if not isinstance(value, dict):
            raise SyncError(f'Expected a JSON object: {path}')
        return value

    def add(self, kind: str, path: Path, value=None):
        self.watch(path)
        if any(op.path == path for op in self.operations):
            raise SyncError(f'Overlapping destinations: {path}')
        self.operations.append(Operation(kind, path, value))

    def write(self, path: Path, text: str):
        current = self.read(path)
        if current == text and path.exists():
            return
        self.add('write', path.resolve() if path.is_symlink() else path, text.encode())

    def write_json(self, path: Path, value: dict):
        # Leave equivalent user formatting alone.
        current = self.json(path)
        if path.exists() and current == value:
            return
        self.write(path, json.dumps(value, indent=2, sort_keys=True) + '\n')

    def verify(self):
        for path, expected in self.expected.items():
            if fingerprint(path) != expected:
                raise SyncError(f'Changed during planning; retry: {path}')

    def apply(self):
        self.verify()
        if not self.operations:
            return
        backup = self.data / '.state/backups' / uuid.uuid4().hex
        backup.mkdir(parents=True, mode=0o700)
        os.chmod(self.data / '.state', 0o700)
        os.chmod(backup.parent, 0o700)
        journal = []
        applied = []
        written = {}
        try:
            # Stage all backups before changing any destinations.
            for index, op in enumerate(self.operations):
                saved = backup / str(index)
                exists = op.path.exists() or op.path.is_symlink()
                if exists:
                    copy_entry(op.path, saved)
                journal.append({'path': str(op.path), 'backup': str(saved) if exists else None})
            atomic_write(backup / 'manifest.json', json.dumps(journal, indent=2).encode())
            self.verify()
            for op, record in zip(self.operations, journal):
                if fingerprint(op.path) != self.expected[op.path]:
                    raise SyncError(f'Changed during application; retry: {op.path}')
                applied.append(record)
                op.path.parent.mkdir(parents=True, exist_ok=True)
                if op.kind == 'write':
                    atomic_write(op.path, op.value)
                elif op.kind == 'copy':
                    shutil.copytree(op.value, op.path, symlinks=True)
                elif op.kind == 'link':
                    remove(op.path)
                    written[op.path] = None
                    op.path.symlink_to(op.value, target_is_directory=True)
                elif op.kind == 'unlink':
                    op.path.unlink()
                written[op.path] = fingerprint(op.path)
            atomic_write(backup / 'complete', b'complete\n')
        except BaseException:
            recovery_errors = []
            for record in reversed(applied):
                path = Path(record['path'])
                try:
                    # Atomic writes can fail before touching the destination.
                    # Do not try deleting an unchanged, unwritable file.
                    current = fingerprint(path)
                    if current == self.expected[path]:
                        continue
                    # Preserve edits made by another process after our write.
                    # A failed operation with an unknown partial result also
                    # needs manual recovery rather than a destructive guess.
                    if path not in written or current != written[path]:
                        recovery_errors.append(path)
                        continue
                    remove(path)
                    if record['backup']:
                        copy_entry(Path(record['backup']), path)
                except OSError:
                    recovery_errors.append(path)
            if recovery_errors:
                raise SyncError(f'Rollback incomplete; restore affected paths using {backup / "manifest.json"}') from None
            raise


@contextmanager
def lock(home: Path):
    folder = home / '.cache/ai-sync'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / 'lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError('Another ai-sync process is applying changes.') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)

"""Atomic local snapshots with an exclusive process-lifetime position lease.

The lock must be held for the entire runtime lifetime, including exchange I/O.
This is a single-host lease, not a distributed lock across different hosts.
"""

import fcntl
import json
import os
from pathlib import Path
import tempfile


class FileStagedStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lease = open(str(self.path)+".lock", "a+b")
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self._lease.close()
            raise

    def load(self) -> dict | None:
        if self._lease.closed:
            raise RuntimeError("Position lease is closed")
        if not self.path.exists():
            return None
        with self.path.open() as stream:
            return json.load(stream)

    def save(self, state: dict) -> None:
        if self._lease.closed:
            raise RuntimeError("Position lease is closed")
        encoded = json.dumps(state, allow_nan=False, sort_keys=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent,
                                             prefix=self.path.name+".", delete=False) as stream:
                temporary = stream.name
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                os.unlink(temporary)

    def close(self) -> None:
        self._lease.close()

    def __enter__(self) -> "FileStagedStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

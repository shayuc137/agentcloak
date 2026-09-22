"""Hold daemon state ownership across processes and PID namespaces."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from collections.abc import Generator

    from agentcloak.core.config import Paths


@contextmanager
def daemon_ownership(paths: Paths) -> Generator[None]:
    paths.ensure_dirs()
    # Keep the inode after unlocking: unlinking lets concurrent starters lock
    # different files with the same name and both believe they own the state.
    with (paths.root / "daemon.lock").open("a+b") as handle:
        os.chmod(handle.name, 0o600)
        if os.name == "nt":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)

            def acquire():
                return msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                return msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                return fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                return fcntl.flock(handle, fcntl.LOCK_UN)

        try:
            acquire()
        except OSError as exc:
            raise AgentBrowserError(
                error="daemon_already_running",
                hint=f"Another daemon owns state directory {paths.root}",
                action="use the running daemon or set a separate AGENTCLOAK_HOME",
            ) from exc
        try:
            yield
        finally:
            release()

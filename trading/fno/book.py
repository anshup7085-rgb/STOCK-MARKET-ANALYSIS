"""
One pass at a time per state directory.

V's state lives in its journal (trading/fno/state.py); book.json is a readable
snapshot the engine rewrites from it. What remains here is the lock that stops
two processes from deciding the same slot.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


class RunnerBusy(RuntimeError):
    """Another pass holds this state directory."""


@contextmanager
def exclusive(state_dir: Path):
    """Two concurrent passes could both enter one cycle; the second waits its turn or gives up."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = (state_dir / "runner.lock").open("a")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerBusy(f"another F&O pass is using {state_dir}") from None
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

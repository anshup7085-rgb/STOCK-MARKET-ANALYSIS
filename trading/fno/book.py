"""
The F&O paper book: starting equity and every V trade, open or closed.

The journal is the evidence (`fno evaluate` reads only the journal); the book is
the engine's working state. Both live in the state directory, and `state/` is
evidence: nothing here edits a closed trade. Writes are atomic, so a crash
mid-save leaves the previous book, never half of one.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

CLOSED = ("SETTLED", "FLATTENED")


@dataclass
class FnoBook:
    starting_equity: float
    created_at: str
    feed: str
    trades: list[dict] = field(default_factory=list)
    cycles: dict[str, dict] = field(default_factory=dict)              # expiry -> what happened to it
    manual_settlements: dict[str, dict] = field(default_factory=dict)  # expiry -> {value, source, entered_at}
    halted: str | None = None                                          # the harm stop, once tripped
    version: int = 1

    def closed(self) -> list[dict]:
        return [t for t in self.trades if t["status"] in CLOSED]

    def realised(self) -> float:
        return sum(t["net"] for t in self.closed())

    def pending_max_loss(self) -> float:
        return sum(t["max_loss"] for t in self.trades if t["status"] == "AWAITING_SETTLEMENT")

    def equity_for_sizing(self) -> float:
        """Section 8: starting equity + settled net P&L - M of trades awaiting a settlement value."""
        return self.starting_equity + self.realised() - self.pending_max_loss()

    def open_trades(self) -> list[dict]:
        return [t for t in self.trades if t["status"] == "OPEN"]

    def trade_for(self, expiry: str) -> dict | None:
        return next((t for t in self.trades if t["expiry"] == expiry), None)


class BookExists(FileExistsError):
    pass


class FnoBookStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def create(self, equity: float, feed: str, created_at: str) -> FnoBook:
        if self.path.exists():
            raise BookExists(f"{self.path} already exists; a paper book is started once")
        if not (equity > 0):
            raise ValueError("starting equity must be positive")
        book = FnoBook(starting_equity=float(equity), created_at=created_at, feed=feed)
        self.save(book)
        return book

    def load(self) -> FnoBook:
        if not self.path.exists():
            raise FileNotFoundError(f"no F&O paper book at {self.path}: run `python run_trader.py fno init` first")
        raw = json.loads(self.path.read_text())
        return FnoBook(**raw)

    def save(self, book: FnoBook) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w") as fh:
            json.dump(asdict(book), fh, indent=1, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


class RunnerBusy(RuntimeError):
    """Another pass or runner holds this state directory."""


@contextmanager
def exclusive(state_dir: Path):
    """One pass at a time per state directory: two runners would double-trade a cycle."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = (state_dir / "runner.lock").open("a")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerBusy(f"another F&O pass or runner is using {state_dir}") from None
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

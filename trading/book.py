"""
The book: the engine's working memory of trades, capital and marks.

The journal is the audit trail; the book is the state. They are kept apart on
purpose — the book is rewritten every cycle and the journal never is. On a live
account the broker is the final truth and the book is reconciled against it.

Capital is tracked as a declared budget, not as the broker's cash balance: the
user decides how much the system may use (`init --equity`), and in live mode
the broker's available cash is a second, independent ceiling on every order.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from market.config import IST
from trading.models import Trade


@dataclass
class Book:
    mode: str
    starting_equity: float
    created_at: str
    trades: dict[str, Trade] = field(default_factory=dict)
    realised_net: float = 0.0
    peak_equity: float = 0.0
    equity_marks: dict[str, float] = field(default_factory=dict)   # date -> end-of-cycle equity
    marks: dict[str, float] = field(default_factory=dict)          # symbol -> last close
    orders_by_day: dict[str, int] = field(default_factory=dict)
    seq: int = 0
    # The completed bar whose signals have already been acted on. A rerun on
    # the same bar — a crash, a second cron firing — must not trade it twice.
    last_signal_bar: str | None = None
    # Live books are bound to the broker their order ids came from.
    broker: str | None = None

    # -- identity ---------------------------------------------------------

    def next_ids(self, symbol: str, signal_bar: str) -> tuple[str, str]:
        """
        (trade_id, broker tag). The tag is the idempotency key: before
        resubmitting anything after a crash, the engine asks the broker whether
        an order with this tag already exists.

        Kite caps tags at 20 alphanumerics. Two characters of book nonce keep a
        re-initialised book from colliding with its predecessor's tags.
        """
        self.seq += 1
        nonce = hashlib.sha256(self.created_at.encode()).hexdigest()[:2].upper()
        day = signal_bar.replace("-", "")[2:8]
        clean = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()[:9]
        tag = f"{nonce}{day}{self.seq % 1000:03d}{clean}"[:20]
        return f"{signal_bar}-{symbol}-{self.seq}", tag

    # -- views ------------------------------------------------------------

    def active(self) -> list[Trade]:
        return [t for t in self.trades.values() if t.active]

    def closed(self) -> list[Trade]:
        return [t for t in self.trades.values() if t.status == "CLOSED"]

    def has_active(self, symbol: str) -> bool:
        return any(t.symbol == symbol for t in self.active())

    def held_qty(self, symbol: str) -> int:
        return sum(
            t.remaining_qty for t in self.trades.values()
            if t.symbol == symbol and t.status in ("OPEN", "EXITING")
        )

    # -- money ------------------------------------------------------------

    def _open_legs_pnl(self) -> float:
        """Gross P&L already booked on the sold part of trades not yet closed."""
        return sum(
            leg.qty * (leg.price - t.entry_price)
            for t in self.trades.values()
            if t.status in ("OPEN", "EXITING") and t.entry_price is not None
            for leg in t.exits
        )

    def unrealised(self) -> float:
        """Marks on what is still held, plus the sold legs of trades not yet
        closed (realised only when the whole trade closes and is charged)."""
        total = self._open_legs_pnl()
        for t in self.trades.values():
            if t.status in ("OPEN", "EXITING") and t.entry_price is not None:
                mark = self.marks.get(t.symbol, t.entry_price)
                total += t.remaining_qty * (mark - t.entry_price)
        return total

    def equity(self) -> float:
        return self.starting_equity + self.realised_net + self.unrealised()

    def committed_capital(self) -> float:
        total = 0.0
        for t in self.trades.values():
            if t.status == "PENDING_ENTRY":
                total += t.qty * t.entry_limit
            elif t.status in ("OPEN", "EXITING") and t.entry_price is not None:
                total += t.remaining_qty * t.entry_price
        return total

    def available_capital(self) -> float:
        # Sold legs of an open trade released qty x entry from committed
        # capital; their P&L is the rest of the cash they brought back.
        return (self.starting_equity + self.realised_net - self.committed_capital()
                + self._open_legs_pnl())

    def open_risk(self) -> float:
        return sum(t.open_risk() for t in self.active())

    def baseline_equity(self, today: str) -> float:
        """Equity at the end of the most recent cycle before `today`."""
        prior = [d for d in self.equity_marks if d < today]
        if not prior:
            return self.starting_equity
        return self.equity_marks[max(prior)]

    def orders_today(self, today: str) -> int:
        return self.orders_by_day.get(today, 0)

    def count_order(self, today: str) -> None:
        self.orders_by_day[today] = self.orders_by_day.get(today, 0) + 1

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trades"] = {k: t.to_dict() for k, t in self.trades.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Book":
        d = dict(d)
        d["trades"] = {k: Trade.from_dict(v) for k, v in d.get("trades", {}).items()}
        return cls(**d)


class BookStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    @contextmanager
    def locked(self, blocking: bool = True):
        """
        Exclusive advisory lock on this book for the length of a
        load-modify-save. `cycle`, `watch`, `flatten` and `record-exit` can run
        in different processes; without this, the slower one saves a stale copy
        over the other's work. Non-blocking callers get False and should skip.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path.with_suffix(".lock"), "a+")
        try:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(fh.fileno(), flags)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    def create(self, mode: str, equity: float) -> Book:
        if self.path.exists():
            raise FileExistsError(
                f"{self.path} already exists. A book is never silently overwritten — "
                "move it aside yourself if you really mean to start again."
            )
        if equity <= 0:
            raise ValueError("starting equity must be positive")
        book = Book(
            mode=mode,
            starting_equity=float(equity),
            created_at=datetime.now(IST).isoformat(),
            peak_equity=float(equity),
        )
        self.save(book)
        return book

    def load(self) -> Book:
        if not self.path.exists():
            raise FileNotFoundError(
                f"No book at {self.path}. Run: python run_trader.py init --mode <mode> --equity <rupees>"
            )
        return Book.from_dict(json.loads(self.path.read_text()))

    def save(self, book: Book) -> None:
        """Write-then-rename, so a crash mid-write never leaves half a book."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(book.to_dict(), indent=2, default=str))
        with tmp.open() as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

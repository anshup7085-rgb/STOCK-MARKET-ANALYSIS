"""
Append-only, hash-chained decision journal.

PROMPT.md: 'Log every decision and every order attempt.' ROADMAP Gate 2:
'>= 60 paper trades logged in real time, with no retrospective edits.'

A plain log satisfies the first and cannot evidence the second. Here each
record carries the SHA-256 of the record before it, so editing, deleting or
reordering any line breaks every hash after it, and `verify()` says where. It
is tamper-evident, not tamper-proof — someone can rewrite the whole chain —
but the promotion gates only ever read a journal that verifies, and a
rewritten chain cannot carry wall-clock timestamps from before it was written.

Every record holds both clocks: `ts`, the wall clock when it was written, and
`asof`, the market date it concerns. A record whose `ts` predates its `asof`
is impossible; one written long after its `asof` was backfilled, and the gate
evaluation can tell.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from market.config import IST

GENESIS = "0" * 64


def _digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class VerifyResult:
    ok: bool
    records: int
    problems: list[str] = field(default_factory=list)


class Journal:
    def __init__(self, path: str | Path, mode: str):
        self.path = Path(path)
        self.mode = mode
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq, self._prev = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        last = None
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS
        rec = json.loads(last)
        return int(rec["seq"]), rec["hash"]

    def append(self, kind: str, asof: str | None = None, **payload) -> dict:
        rec = {
            "seq": self._seq + 1,
            "ts": datetime.now(IST).isoformat(),
            "asof": asof,
            "mode": self.mode,
            "type": kind,
            "payload": payload,
            "prev": self._prev,
        }
        rec["hash"] = _digest(rec)
        line = json.dumps(rec, sort_keys=True, default=str)
        with self.path.open("a") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seq, self._prev = rec["seq"], rec["hash"]
        return rec

    def records(self, kinds: tuple[str, ...] | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if kinds is None or rec.get("type") in kinds:
                    out.append(rec)
        return out

    def verify(self) -> VerifyResult:
        return verify_file(self.path)


def verify_file(path: str | Path) -> VerifyResult:
    p = Path(path)
    if not p.exists():
        return VerifyResult(True, 0, ["journal does not exist yet"])
    problems: list[str] = []
    prev = GENESIS
    expected_seq = 1
    n = 0
    with p.open() as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            n += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"line {lineno}: not valid JSON")
                prev = None
                continue
            if rec.get("seq") != expected_seq:
                problems.append(f"line {lineno}: seq {rec.get('seq')} where {expected_seq} expected")
            if prev is not None and rec.get("prev") != prev:
                problems.append(f"line {lineno}: chain broken — prev hash does not match")
            if _digest(rec) != rec.get("hash"):
                problems.append(f"line {lineno}: content does not match its hash (edited)")
            expected_seq = int(rec.get("seq", expected_seq)) + 1
            prev = rec.get("hash")
    return VerifyResult(not problems, n, problems)

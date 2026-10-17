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

import fcntl
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


def _last_record(fh) -> tuple[int, str]:
    """
    (seq, hash) of the last COMPLETE record, read from the end of the file.

    A torn final line — disk full, or a kill mid-write — is skipped rather than
    crashing every later command. The next record chains from the last whole
    one, and `verify()` still reports the torn line, so the damage stays visible
    without stopping the system from managing open positions.
    """
    fh.seek(0, os.SEEK_END)
    end = fh.tell()
    block, data = 8192, b""
    pos = end
    while pos > 0:
        step = min(block, pos)
        pos -= step
        fh.seek(pos)
        data = fh.read(step) + data
        lines = data.splitlines()
        # The first line may be cut by the block boundary unless we are at 0.
        candidates = lines if pos == 0 else lines[1:]
        for raw in reversed(candidates):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                return int(rec["seq"]), str(rec["hash"])
            except (ValueError, KeyError, TypeError):
                continue
        block *= 2
    return 0, GENESIS


class Journal:
    def __init__(self, path: str | Path, mode: str):
        self.path = Path(path)
        self.mode = mode
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, asof: str | None = None, **payload) -> dict:
        # Lock, re-read the tail, write, unlock. The tail is NOT cached: `kill`
        # appending while `watch` runs would otherwise leave watch chaining from
        # a stale hash and break the chain for good.
        with self.path.open("ab+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                seq, prev = _last_record(fh)
                rec = {
                    "seq": seq + 1,
                    "ts": datetime.now(IST).isoformat(),
                    "asof": asof,
                    "mode": self.mode,
                    "type": kind,
                    "payload": payload,
                    "prev": prev,
                }
                rec["hash"] = _digest(rec)
                line = json.dumps(rec, sort_keys=True, default=str)
                fh.seek(0, os.SEEK_END)
                if fh.tell() > 0:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        fh.write(b"\n")       # never glue a record onto a torn line
                fh.write(line.encode() + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return rec

    def records(self, kinds: tuple[str, ...] | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue      # a torn line; verify() reports it
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

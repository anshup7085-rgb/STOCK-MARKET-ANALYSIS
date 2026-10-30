"""
V-H: strategy V's historical track on NSE's daily files (research/PREREGISTRATION_4.md).

This module holds the binding: the SHA-256 of the registration text. Every V-H computation
calls check_registration() first and refuses to run against any other text, so the rules
cannot change after the data is seen. The downloader and the two implementations come
after the binding (PREREGISTRATION_4 section 8).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REGISTRATION = Path(__file__).resolve().parents[2] / "research" / "PREREGISTRATION_4.md"
REGISTRATION_SHA256 = "eacf2ec247bf3987305dbc31e4d38140a3e40257e3200f856b8930693927b55a"


class RegistrationMismatch(RuntimeError):
    """The registration text is not the one bound on 2026-10-30."""


def check_registration(path: Path = REGISTRATION) -> str:
    got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if got != REGISTRATION_SHA256:
        raise RegistrationMismatch(f"{path} has SHA-256 {got}, not the bound {REGISTRATION_SHA256}: V-H refuses to run")
    return got

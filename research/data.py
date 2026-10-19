"""
Fetch the research inputs once and freeze them.

Every computation in research/ reads these CSVs, never the network, so a result
can be reproduced from the exact inputs it used. `manifest()` gives the SHA-256
of each file for the results report.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent / "cache"
INDEX = "^NSEI"


def _name(symbol: str) -> str:
    return symbol.replace("^", "IDX_").replace("&", "_AND_") + ".csv"


def fetch(symbols: list[str], lookback: int, provider_name: str = "yfinance") -> dict[str, str]:
    """Download through the project's provider and write one CSV per symbol."""
    from market.providers import DataUnavailable, get_provider

    prov = get_provider(provider_name)
    CACHE.mkdir(parents=True, exist_ok=True)
    errors = {}
    for s in symbols:
        try:
            df = prov.get_bars(s, "1d", lookback).df
        except DataUnavailable as exc:
            errors[s] = str(exc)
            continue
        out = df.copy()
        out.index = pd.DatetimeIndex(out.index).tz_localize(None) if getattr(out.index, "tz", None) else out.index
        out.index.name = "date"
        out.to_csv(CACHE / _name(s))
    return errors


def corporate_action_fixes(df: pd.DataFrame) -> list[tuple[str, float, str]]:
    """
    (date, factor, label) for every overnight jump that matches a standard
    split/bonus factor — the fingerprint of history the provider forgot to
    adjust. "Extreme gap" flags with no factor match are NOT fixed: those are
    real moves or bad ticks, and guessing which would be fabrication.
    """
    from market.corporate_actions import _FACTORS, suspect_corporate_actions

    out = []
    for a in suspect_corporate_actions(df, lookback=len(df)):
        factor = min(_FACTORS, key=lambda f: abs(a.ratio / f - 1))
        if abs(a.ratio / factor - 1) <= 0.03:
            out.append((a.date, factor, _FACTORS[factor]))
    return out


def load(symbol: str, adjust: bool = True) -> pd.DataFrame:
    """
    The cached bars, with any unadjusted split/bonus back-adjusted by its exact
    standard factor (prices x factor, volume / factor, before the event date).
    This rule was fixed before any strategy result was computed; the results
    report lists every adjustment it made.
    """
    df = pd.read_csv(CACHE / _name(symbol), index_col="date", parse_dates=["date"]).sort_index()
    df = df.astype(float)
    if adjust:
        for day, factor, _ in corporate_action_fixes(df):
            before = df.index < pd.Timestamp(day)
            df.loc[before, ["open", "high", "low", "close"]] *= factor
            df.loc[before, "volume"] /= factor
    return df


def placeholder_rows(df: pd.DataFrame) -> pd.DatetimeIndex:
    """
    Rows Yahoo fills in for a day it has no trade data for: zero volume and
    open = high = low = close = the previous close. They look like real prices
    to dropna() and ffill(), so a strategy executing on one trades at a stale
    price. Reported, not removed: the pre-registered runs used them as they are.
    """
    prev = df["close"].shift(1)
    flat = (df["open"] == df["close"]) & (df["high"] == df["close"]) & (df["low"] == df["close"])
    return df.index[(df["volume"] == 0) & flat & (df["close"] == prev)]


def available() -> list[str]:
    return sorted(p.name for p in CACHE.glob("*.csv"))


def manifest() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(CACHE.glob("*.csv"))}


MANIFEST = Path(__file__).resolve().parent / "MANIFEST.sha256"


def manifest_mismatches() -> list[str]:
    """Files that differ from, or are missing against, the committed MANIFEST.sha256."""
    if not MANIFEST.exists():
        return ["MANIFEST.sha256 missing"]
    want = {}
    for line in MANIFEST.read_text().splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            want[name.strip()] = digest
    have = manifest()
    out = [f"{n}: missing" for n in sorted(set(want) - set(have))]
    out += [f"{n}: differs" for n in sorted(set(want) & set(have)) if want[n] != have[n]]
    out += [f"{n}: not in manifest" for n in sorted(set(have) - set(want))]
    return out


if __name__ == "__main__":
    import sys

    from market.universe import DEFAULT_UNIVERSE

    if available() and "--refetch" not in sys.argv:
        # A re-fetch downloads a window ending today, so it cannot reproduce the
        # frozen inputs; it would silently replace them.
        print(f"[refused] {CACHE} already holds {len(available())} files, the frozen inputs. "
              "Re-fetching replaces them with a different window; pass --refetch to do it anyway.",
              file=sys.stderr)
        raise SystemExit(2)
    errs = fetch([INDEX], lookback=10_000)
    errs.update(fetch(DEFAULT_UNIVERSE, lookback=2500))
    print(f"cached {len(available())} files in {CACHE}")
    for s, e in errs.items():
        print(f"  missing {s}: {e[:120]}")

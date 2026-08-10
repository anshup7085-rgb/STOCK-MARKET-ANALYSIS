"""
Synthetic provider — for verifying the install and the wiring with no feed,
no credentials and no network.

Never use it for a real decision. Its freshness is reported as 'unknown' and
every downstream report will carry that flag, which is the point.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from market.config import IST
from market.providers.base import Bars, DataProvider


class MockProvider(DataProvider):
    name = "MOCK — synthetic data, NOT REAL"
    supports_intraday = False
    supports_options = False

    def get_bars(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> Bars:
        seed = abs(hash(symbol)) % (2**31)
        rng = np.random.default_rng(seed)
        n = min(lookback, 300)

        start = 200 + (seed % 2000)
        drift = rng.normal(0.0006, 0.0015)
        rets = rng.normal(drift, 0.016, n)
        close = start * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, 0.005, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.007, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.007, n)))
        vol = rng.integers(400_000, 4_000_000, n)

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
            index=pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n),
        )
        return Bars(
            symbol=symbol,
            df=df,
            timeframe=timeframe,
            freshness="unknown",
            source=self.name,
            fetched_at=datetime.now(IST),
        )

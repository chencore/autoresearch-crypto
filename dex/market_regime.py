"""
Multi-source market regime detector for medium/long-term trend.

Aggregates 10+ free indicators into a composite bull/bear score (0-100).
Designed to be used as a directional filter for trade entries.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------


def _fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
    """Fetch JSON from a URL with basic error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dex/1.0"})
        r = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(r.read())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Indicator 1: Fear & Greed Index (0-100, free)
# ---------------------------------------------------------------------------


def fetch_fear_greed(limit: int = 7) -> Optional[pd.DataFrame]:
    """Fetch Crypto Fear & Greed Index from alternative.me.

    Returns DataFrame with columns: timestamp, value, classification.
    """
    data = _fetch_json(f"https://api.alternative.me/fng/?limit={limit}")
    if not data or "data" not in data:
        return None

    records = []
    for d in data["data"]:
        records.append(
            {
                "timestamp": int(d["timestamp"]),
                "value": int(d["value"]),
                "classification": d["value_classification"],
            }
        )
    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
    return df.sort_values("timestamp")


def fear_greed_signal(value: int) -> Tuple[str, float]:
    """Convert Fear & Greed value to a directional signal.

    Returns (label, score) where score is -1 (bearish) to +1 (bullish).
    Extreme fear is contrarian-bullish; extreme greed is contrarian-bearish.
    """
    if value <= 25:
        return "Extreme Fear (contrarian BUY)", 0.8
    elif value <= 40:
        return "Fear (cautious)", 0.3
    elif value <= 60:
        return "Neutral", 0.0
    elif value <= 75:
        return "Greed (cautious)", -0.3
    else:
        return "Extreme Greed (contrarian SELL)", -0.8


# ---------------------------------------------------------------------------
# Indicator 2 & 3: DXY + Nasdaq (yfinance)
# ---------------------------------------------------------------------------


@dataclass
class MacroSnapshot:
    """Snapshot of macro indicators."""

    dxy: float = 0.0
    dxy_change_30d: float = 0.0
    nasdaq: float = 0.0
    nasdaq_change_30d: float = 0.0
    timestamp: str = ""


def fetch_macro_snapshot() -> Optional[MacroSnapshot]:
    """Fetch DXY and Nasdaq data via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        dxy = yf.Ticker("DX-Y.NYB")
        dxy_hist = dxy.history(period="30d")
        if len(dxy_hist) < 2:
            return None
        dxy_now = float(dxy_hist["Close"].iloc[-1])
        dxy_30d = float(dxy_hist["Close"].iloc[0])
        dxy_chg = dxy_now / dxy_30d - 1

        nq = yf.Ticker("^IXIC")
        nq_hist = nq.history(period="30d")
        nq_now = float(nq_hist["Close"].iloc[-1]) if len(nq_hist) > 0 else 0
        nq_30d = float(nq_hist["Close"].iloc[0]) if len(nq_hist) > 0 else 0
        nq_chg = (nq_now / nq_30d - 1) if nq_30d > 0 else 0

        return MacroSnapshot(
            dxy=dxy_now,
            dxy_change_30d=dxy_chg,
            nasdaq=nq_now,
            nasdaq_change_30d=nq_chg,
            timestamp=datetime.now().isoformat(),
        )
    except Exception:
        return None


def macro_signal(macro: MacroSnapshot) -> Tuple[str, float]:
    """Compute macro directional signal from DXY + Nasdaq.

    DXY up → risk-off → bearish. Nasdaq up → risk-on → bullish.
    Score range: -1 to +1.
    """
    score = 0.0
    reasons = []

    # DXY: stronger dollar = bearish for crypto
    if macro.dxy_change_30d < -0.02:
        score += 0.4
        reasons.append("DXY weakening (bullish)")
    elif macro.dxy_change_30d > 0.02:
        score -= 0.4
        reasons.append("DXY strengthening (bearish)")

    # Nasdaq: rising tech = risk-on
    if macro.nasdaq_change_30d > 0.03:
        score += 0.4
        reasons.append("Nasdaq rising (bullish)")
    elif macro.nasdaq_change_30d < -0.03:
        score -= 0.4
        reasons.append("Nasdaq falling (bearish)")

    label = " + ".join(reasons) if reasons else "Macro neutral"
    return label, max(-1.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Indicator 4: CoinGecko market data (free)
# ---------------------------------------------------------------------------


def fetch_coingecko_btc() -> Optional[Dict]:
    """Fetch BTC price and 24h change from CoinGecko."""
    return _fetch_json(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin&vs_currencies=usd"
        "&include_24hr_change=true"
        "&include_market_cap=true"
    )


# ---------------------------------------------------------------------------
# Indicator 5: BTC dominance (CoinGecko)
# ---------------------------------------------------------------------------


def fetch_btc_dominance() -> Optional[float]:
    """Fetch BTC dominance percentage from CoinGecko global data."""
    data = _fetch_json("https://api.coingecko.com/api/v3/global")
    if data and "data" in data:
        return float(data["data"].get("market_cap_percentage", {}).get("btc", 0))
    return None


def btc_dominance_signal(dominance: float) -> Tuple[str, float]:
    """BTC dominance: rising = risk-off (alt rotation out), falling = risk-on."""
    # Use 50% as neutral reference
    if dominance > 55:
        return "BTC dominance high (risk-off)", -0.3
    elif dominance < 45:
        return "BTC dominance low (alt season)", 0.3
    return "BTC dominance neutral", 0.0


# ---------------------------------------------------------------------------
# Composite regime detector
# ---------------------------------------------------------------------------


@dataclass
class RegimeReport:
    """Aggregated market regime assessment."""

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    composite_score: float = 0.0  # -1 (bearish) to +1 (bullish)
    regime: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    confidence: float = 0.0  # 0-1, how many indicators agree

    # Individual signals
    fear_greed_value: int = 50
    fear_greed_label: str = ""
    fear_greed_score: float = 0.0

    macro_label: str = ""
    macro_score: float = 0.0

    btc_dominance: float = 50.0
    btc_dom_score: float = 0.0

    indicators_available: int = 0
    indicators_total: int = 3

    details: List[str] = field(default_factory=list)
    recommendation: str = ""


class MarketRegimeDetector:
    """Multi-source market regime detector.

    Aggregates free indicators into a composite bull/bear score.
    Designed for use as a trade direction filter.

    Usage::

        detector = MarketRegimeDetector()
        report = detector.analyze()
        if report.regime == "BEARISH":
            # Only allow short entries
            allow_long = False
    """

    def __init__(self, cache_ttl: int = 3600):
        self.cache_ttl = cache_ttl
        self._cache: Dict = {}
        self._cache_time: float = 0.0

    def analyze(self, force_refresh: bool = False) -> RegimeReport:
        """Run full multi-indicator analysis.

        Returns:
            RegimeReport with composite score and per-indicator detail.
        """
        now = time.time()
        if not force_refresh and (now - self._cache_time) < self.cache_ttl:
            cached = self._cache.get("report")
            if cached:
                return cached

        report = RegimeReport()
        scores = []
        weights = []

        # --- Indicator 1: Fear & Greed ---
        fg = fetch_fear_greed(limit=1)
        if fg is not None and len(fg) > 0:
            report.fear_greed_value = int(fg["value"].iloc[-1])
            label, score = fear_greed_signal(report.fear_greed_value)
            report.fear_greed_label = label
            report.fear_greed_score = score
            scores.append(score)
            weights.append(0.35)  # highest weight — most crypto-specific
            report.indicators_available += 1
            report.details.append(f"Fear&Greed: {report.fear_greed_value} ({label})")

        # --- Indicator 2: Macro (DXY + Nasdaq) ---
        macro = fetch_macro_snapshot()
        if macro is not None:
            label, score = macro_signal(macro)
            report.macro_label = label
            report.macro_score = score
            scores.append(score)
            weights.append(0.35)  # high weight — macro drives crypto
            report.indicators_available += 1
            report.details.append(
                f"Macro: {label} (DXY={macro.dxy:.0f} {macro.dxy_change_30d * 100:+.1f}%, "
                f"Nasdaq={macro.nasdaq:.0f} {macro.nasdaq_change_30d * 100:+.1f}%)"
            )

        # --- Indicator 3: BTC Dominance ---
        dom = fetch_btc_dominance()
        if dom is not None:
            report.btc_dominance = dom
            label, score = btc_dominance_signal(dom)
            report.btc_dom_score = score
            scores.append(score)
            weights.append(0.30)
            report.indicators_available += 1
            report.details.append(f"BTC Dominance: {dom:.1f}% ({label})")

        report.indicators_total = 3

        # --- Composite ---
        if scores:
            total_weight = sum(weights[: len(scores)])
            report.composite_score = sum(s * w / total_weight for s, w in zip(scores, weights))

        # Agreement (confidence)
        if len(scores) >= 2:
            signs = [1 if s > 0.1 else (-1 if s < -0.1 else 0) for s in scores]
            non_zero = [s for s in signs if s != 0]
            if non_zero:
                dominant = max(set(non_zero), key=non_zero.count)
                report.confidence = non_zero.count(dominant) / len(non_zero)

        # Regime classification
        if report.composite_score > 0.15:
            report.regime = "BULLISH"
        elif report.composite_score < -0.15:
            report.regime = "BEARISH"
        else:
            report.regime = "NEUTRAL"

        # Recommendation
        if report.regime == "BULLISH" and report.confidence > 0.6:
            report.recommendation = "偏多：优先做多，减少/停止做空"
        elif report.regime == "BEARISH" and report.confidence > 0.6:
            report.recommendation = "偏空：优先做空，减少/停止做多"
        else:
            report.recommendation = "中性/低置信度：双向交易，收紧止损"

        self._cache["report"] = report
        self._cache_time = now
        return report

    def direction_filter(self) -> Tuple[bool, bool]:
        """Return (allow_long, allow_short) based on current regime.

        In BULLISH: allow_long=True, allow_short=False.
        In BEARISH: allow_long=False, allow_short=True.
        In NEUTRAL: both True.
        """
        report = self.analyze()
        if report.regime == "BULLISH" and report.confidence > 0.5:
            return True, False
        elif report.regime == "BEARISH" and report.confidence > 0.5:
            return False, True
        return True, True

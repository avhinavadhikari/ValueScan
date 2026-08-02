"""
Finnhub Data Provider for ValueScan
Replaces FMP — free tier, 60 calls/minute, no credit card needed
API docs: https://finnhub.io/docs/api
"""

import json
import time
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Optional


class FinnhubDataProvider:
    """
    Fetches stock fundamentals from Finnhub free API.
    Drop-in replacement for USDataProvider.
    Same interface: get_metrics(ticker) -> Optional[StockMetrics]
    """

    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, endpoint: str, params: dict = None) -> dict:
        params = params or {}
        params["token"] = self.api_key
        url = f"{self.BASE}{endpoint}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ValueScan/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"  [Finnhub error] {endpoint}: {e}")
            return {}

    def get_metrics(self, ticker: str):
        """Fetch all metrics for one ticker using Finnhub API."""
        from scanner import StockMetrics

        print(f"  Fetching {ticker}...")

        # 1. Quote (price, 52w high/low)
        quote = self._get("/quote", {"symbol": ticker})
        if not quote or quote.get("c", 0) == 0:
            return None

        # 2. Company profile (sector, industry, market cap)
        profile = self._get("/stock/profile2", {"symbol": ticker})

        # 3. Basic financials (PE, PB, ROE, margins etc)
        fins = self._get("/stock/metric", {"symbol": ticker, "metric": "all"})
        m = fins.get("metric", {})

        # 4. Insider sentiment (insider ownership proxy)
        insider = self._get("/stock/insider-sentiment", {
            "symbol": ticker,
            "from": "2025-01-01",
            "to": datetime.now().strftime("%Y-%m-%d")
        })

        mktcap = (profile.get("marketCapitalization") or 0)  # already in millions on Finnhub
        mktcap_b = round(mktcap / 1000, 2) if mktcap else 0

        price      = quote.get("c", 0)
        week52high = quote.get("h", 0) or m.get("52WeekHigh", 0)
        week52low  = quote.get("l", 0) or m.get("52WeekLow", 0)

        # Calculate margin of safety proxy
        mos = 0
        if week52high and price:
            mos = round((1 - price / week52high) * 100, 1)

        # Insider buying signal from sentiment
        insider_data = insider.get("data", [])
        net_buying = sum(d.get("change", 0) for d in insider_data) if insider_data else 0
        buyback = net_buying > 0  # positive = insiders buying

        return StockMetrics(
            ticker=ticker,
            company=profile.get("name", ticker),
            sector=profile.get("finnhubIndustry", "Unknown"),
            industry=profile.get("finnhubIndustry", "Unknown"),
            region="US",
            currency="USD",
            market_cap_b=mktcap_b,
            pe_ratio=self._safe(m.get("peBasicExclExtraTTM") or m.get("peTTM")),
            pb_ratio=self._safe(m.get("pbQuarterly") or m.get("pbAnnual")),
            roe=self._safe_pct(m.get("roeTTM")),
            fcf_yield=self._safe_pct(m.get("fcfYieldTTM")),
            debt_equity=self._safe(m.get("totalDebt/totalEquityAnnual")),
            current_ratio=self._safe(m.get("currentRatioAnnual")),
            revenue_growth=self._safe_pct(m.get("revenueGrowthTTMYoy")),
            gross_margin=self._safe_pct(m.get("grossMarginTTM")),
            dividend_yield=self._safe_pct(m.get("dividendYieldIndicatedAnnual")),
            price=price,
            fifty_two_week_low=week52low,
            fifty_two_week_high=week52high,
            timestamp=datetime.utcnow().isoformat(),
            insider_ownership_pct=self._safe(m.get("insiderOwnershipPercentage")),
            shares_buyback=buyback,
            net_debt_b=self._safe(m.get("netDebtAnnual")),
        )

    def _safe(self, v) -> Optional[float]:
        try:
            return round(float(v), 2) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _safe_pct(self, v) -> Optional[float]:
        try:
            f = float(v)
            return round(f * 100 if abs(f) < 2 else f, 2) if v is not None else None
        except (TypeError, ValueError):
            return None

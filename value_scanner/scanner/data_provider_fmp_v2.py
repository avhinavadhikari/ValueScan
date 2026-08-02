"""
FMP Data Provider v2 — uses new stable endpoints (post Aug 2025)
Base URL: https://financialmodelingprep.com/stable/
"""
import json, time, urllib.request, urllib.parse
from datetime import datetime
from typing import Optional


class FMPv2DataProvider:
    BASE = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, endpoint: str, params: dict = None) -> dict:
        params = params or {}
        params["apikey"] = self.api_key
        url = f"{self.BASE}{endpoint}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ValueScan/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"  [FMP error] {endpoint}: {e}")
            return {}

    def get_metrics(self, ticker: str):
        from scanner import StockMetrics
        print(f"  Fetching {ticker}...")

        # New stable endpoints
        quote    = self._get(f"/quote", {"symbol": ticker})
        profile  = self._get(f"/profile", {"symbol": ticker})
        ratios   = self._get(f"/ratios-ttm", {"symbol": ticker})
        metrics  = self._get(f"/key-metrics-ttm", {"symbol": ticker})

        # Handle list responses
        q = quote[0]   if isinstance(quote,   list) and quote   else (quote   if isinstance(quote,   dict) else {})
        p = profile[0] if isinstance(profile, list) and profile else (profile if isinstance(profile, dict) else {})
        r = ratios[0]  if isinstance(ratios,  list) and ratios  else (ratios  if isinstance(ratios,  dict) else {})
        m = metrics[0] if isinstance(metrics, list) and metrics else (metrics if isinstance(metrics, dict) else {})

        if not q.get("price") and not q.get("marketCap"):
            return None

        mktcap_b = round((q.get("marketCap") or p.get("mktCap") or 0) / 1e9, 2)
        price    = q.get("price") or q.get("previousClose") or 0
        hi52     = q.get("yearHigh")  or q.get("52WeekHigh")
        lo52     = q.get("yearLow")   or q.get("52WeekLow")

        # Detect buybacks from shares outstanding change
        buyback = None
        sh_curr = q.get("sharesOutstanding")
        if sh_curr and m.get("weightedAverageSharesDilutedTTM"):
            buyback = float(sh_curr) < float(m["weightedAverageSharesDilutedTTM"]) * 1.001

        return StockMetrics(
            ticker=ticker,
            company=p.get("companyName", ticker),
            sector=p.get("sector", "Unknown"),
            industry=p.get("industry", "Unknown"),
            region="US", currency="USD",
            market_cap_b=mktcap_b,
            pe_ratio=self._safe(q.get("pe") or m.get("peRatioTTM")),
            pb_ratio=self._safe(m.get("pbRatioTTM")),
            roe=self._safe_pct(m.get("roeTTM")),
            fcf_yield=self._safe_pct(m.get("fcfYieldTTM")),
            debt_equity=self._safe(r.get("debtEquityRatioTTM")),
            current_ratio=self._safe(r.get("currentRatioTTM")),
            revenue_growth=self._safe_pct(m.get("revenueGrowthTTM")),
            gross_margin=self._safe_pct(r.get("grossProfitMarginTTM")),
            dividend_yield=self._safe_pct(m.get("dividendYieldTTM") or q.get("lastAnnualDividend")),
            price=price,
            fifty_two_week_low=lo52,
            fifty_two_week_high=hi52,
            timestamp=datetime.utcnow().isoformat(),
            insider_ownership_pct=self._safe(m.get("insiderOwnershipTTM")),
            shares_buyback=buyback,
            net_debt_b=self._safe((m.get("netDebtTTM") or 0) / 1e9 if m.get("netDebtTTM") else None),
        )

    def _safe(self, v) -> Optional[float]:
        try: return round(float(v), 2) if v is not None else None
        except: return None

    def _safe_pct(self, v) -> Optional[float]:
        try:
            f = float(v)
            return round(f * 100 if abs(f) < 2 else f, 2) if v is not None else None
        except: return None

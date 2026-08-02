"""
ValueScan - US Value Stock Scanner
Built on: Graham · Fisher · Buffett/Munger · Lynch · Klarman · Greenblatt

Architecture is global-expansion ready:
  - Each region is a separate DataProvider class
  - Currency normalisation layer is pre-wired (USD passthrough for US)
  - Scoring engine is region-agnostic
"""

import json
import time
import os
from datetime import datetime, date
from dataclasses import dataclass, asdict
from typing import Optional
import urllib.request
import urllib.parse


# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

CONFIG = {
    # API keys — set via environment variables
    "FMP_API_KEY": os.getenv("FMP_API_KEY", "demo"), "FINNHUB_API_KEY": os.getenv("FINNHUB_API_KEY", ""),         # Financial Modeling Prep
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),  # Claude rationale

    # Value framework thresholds (Graham / Greenblatt / Lynch / Buffett)
    "filters": {
        "max_pe":                20,    # P/E below 20 (Graham: ideally < 15)
        "max_pb":               3.0,    # Price-to-book below 3
        "min_roe":               10,    # Return on equity above 10%
        "min_fcf_yield":        3.0,    # Free cash flow yield above 3%
        "max_debt_equity":      0.5,    # LOW DEBT: debt/equity below 0.5 (Buffett quality filter)
        "min_current_ratio":    1.2,    # Current ratio above 1.2 (Graham safety)
        "max_market_cap_b":     100,    # Under $100B market cap
        "min_market_cap_b":     0.5,    # Over $500M (avoid micro-caps)
        "min_revenue_growth":   -5,     # Revenue not collapsing (>-5% YoY)
        "min_gross_margin":      20,    # Gross margin above 20%
        # ── New quality criteria (Lynch / Buffett) ──
        "min_insider_ownership": 2.0,   # Insiders hold >= 2% — skin in the game
        "require_buybacks":      True,  # Company reducing share count — management confidence
    },

    # Moat keywords for sector-based moat scoring
    # Expand this knowledge base with your own research
    "moat_sectors": {
        "high": [
            "Software", "Tobacco", "Specialty Chemicals", "Insurance",
            "Credit Services", "Beverages", "Drug Manufacturers",
            "Diagnostics & Research", "Information Technology Services",
        ],
        "medium": [
            "Consumer Defensive", "Utilities", "Real Estate",
            "Aerospace & Defense", "Industrial Distribution",
            "Medical Devices", "Packaged Foods",
        ],
        "low": [
            "Airlines", "Auto Manufacturers", "Steel",
            "Oil & Gas E&P", "Restaurants", "Retail",
        ],
    },

    # Sector correlation groups — no two stocks from same group
    # Edit to control portfolio diversification
    "correlation_groups": {
        "US_BANKS":        ["Banks", "Credit Services", "Insurance"],
        "ENERGY":          ["Oil & Gas", "Energy"],
        "CONSUMER_STAPLES":["Packaged Foods", "Beverages", "Household Products", "Tobacco"],
        "PHARMA":          ["Drug Manufacturers", "Biotechnology", "Diagnostics & Research"],
        "TECH_PLATFORM":   ["Software", "Internet Content", "Information Technology Services"],
        "INDUSTRIAL":      ["Aerospace & Defense", "Industrial Distribution", "Specialty Chemicals"],
        "REAL_ESTATE":     ["Real Estate", "REITs"],
        "DEFENSE":         ["Aerospace & Defense"],
        "MEDIA":           ["Entertainment", "Publishing"],
    },

    # Universe of stocks to scan
    # Expand by adding tickers — for global, add exchange prefix e.g. "TSE:7203"
    "universe": {
        "US": [
            # Consumer
            "STZ", "CPB", "CL", "GIS", "BTI", "LULU", "MO", "KHC", "HSY", "CHD",
            # Healthcare
            "BMY", "MRK", "HOLX", "TECH", "VTRS", "HUM", "CVS", "MCK", "BDX",
            # Industrial / Defense
            "HII", "NVT", "WSO", "RPM", "AME", "BWA", "MLM", "VMC", "EMR", "ITW",
            # Finance / Insurance
            "MCO", "FICO", "EG", "WTW", "CB", "AFL", "MET", "PRU", "PFG",
            # Tech / Software
            "PANW", "CDNS", "ANSS", "PTC", "MANH", "NICE", "EPAM", "LDOS",
            # Energy
            "GEV", "PSX", "VLO", "OKE", "WMB", "LNG",
            # Real Estate
            "IRT", "NNN", "O", "STAG", "EXR",
        ],
        # GLOBAL EXPANSION — pre-wired, add tickers when ready
        # "EU": [],    # European stocks (EODHD: "SAP.XETRA", "RELX.LSE")
        # "JP": [],    # Japan (EODHD: "7203.TSE")
        # "KR": [],    # Korea (EODHD: "005930.KSC")
        # "AU": [],    # Australia (EODHD: "BHP.AU")
    },
}


# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class StockMetrics:
    ticker: str
    company: str
    sector: str
    industry: str
    region: str              # "US", "EU", "JP" — for global expansion
    currency: str            # "USD", "EUR", "JPY" — normalised to USD
    market_cap_b: float      # in USD billions
    pe_ratio: Optional[float]
    pb_ratio: Optional[float]
    roe: Optional[float]     # %
    fcf_yield: Optional[float]  # %
    debt_equity: Optional[float]
    current_ratio: Optional[float]
    revenue_growth: Optional[float]  # % YoY
    gross_margin: Optional[float]    # %
    dividend_yield: Optional[float]  # %
    price: float
    fifty_two_week_low: Optional[float]
    fifty_two_week_high: Optional[float]
    timestamp: str
    # ── New quality signals ──
    insider_ownership_pct: Optional[float]   # % shares held by insiders
    shares_buyback: Optional[bool]           # True if share count decreased YoY
    net_debt_b: Optional[float]              # Net debt in $B (negative = net cash)

@dataclass
class ScoredStock:
    metrics: StockMetrics
    passes_filters: bool
    filter_failures: list
    value_score: float       # 0–100
    moat_score: str          # "high" / "medium" / "low" / "unknown"
    margin_of_safety: float  # % below 52-week high
    correlation_group: str
    rationale: str           # Generated by Claude


# ─────────────────────────────────────────────
# DATA PROVIDER — US (Financial Modeling Prep)
# ─────────────────────────────────────────────

class USDataProvider:
    """
    Fetches fundamental data from Financial Modeling Prep API.
    Global expansion: create EUDataProvider, JPDataProvider etc.
    with the same interface (get_metrics method).
    """

    BASE = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, endpoint: str, params: dict = None) -> dict:
        params = params or {}
        params["apikey"] = self.api_key
        url = f"{self.BASE}{endpoint}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print(f"  [API error] {endpoint}: {e}")
            return {}

    def get_metrics(self, ticker: str) -> Optional[StockMetrics]:
        """Fetch all metrics for one ticker. Returns None on failure."""
        print(f"  Fetching {ticker}...")

        # Quote (price, market cap)
        quote = self._get(f"/quote/{ticker}")
        if not quote or not isinstance(quote, list):
            return None
        q = quote[0]

        # Key metrics (P/E, P/B, ROE, FCF yield etc.)
        km = self._get(f"/key-metrics-ttm/{ticker}")
        km = km[0] if km and isinstance(km, list) else {}

        # Financial ratios (current ratio, debt/equity, margins)
        ratios = self._get(f"/ratios-ttm/{ticker}")
        ratios = ratios[0] if ratios and isinstance(ratios, list) else {}

        # Company profile (sector, industry)
        profile = self._get(f"/profile/{ticker}")
        profile = profile[0] if profile and isinstance(profile, list) else {}

        # Insider ownership
        insider = self._get(f"/insider-roaster-statistic/{ticker}")
        insider = insider[0] if insider and isinstance(insider, list) else {}

        # Shares outstanding history (to detect buybacks)
        shares_hist = self._get(f"/historical-shares-outstanding/{ticker}")
        shares_hist = shares_hist if isinstance(shares_hist, list) else []

        mktcap = q.get("marketCap", 0) or 0

        # Detect buybacks: shares outstanding decreased over last year
        buyback = None
        if len(shares_hist) >= 2:
            try:
                recent = float(shares_hist[0].get("outstandingShares", 0))
                prior  = float(shares_hist[-1].get("outstandingShares", 1))
                buyback = recent < prior * 0.999  # at least 0.1% reduction
            except (TypeError, ValueError, ZeroDivisionError):
                buyback = None

        # Net debt
        net_debt = None
        try:
            total_debt = float(km.get("netDebtTTM", 0) or 0)
            net_debt = round(total_debt / 1e9, 2)
        except (TypeError, ValueError):
            pass

        return StockMetrics(
            ticker=ticker,
            company=q.get("name", ticker),
            sector=profile.get("sector", "Unknown"),
            industry=profile.get("industry", "Unknown"),
            region="US",
            currency="USD",
            market_cap_b=round(mktcap / 1e9, 2),
            pe_ratio=self._safe(km.get("peRatioTTM") or q.get("pe")),
            pb_ratio=self._safe(km.get("pbRatioTTM")),
            roe=self._safe_pct(km.get("roeTTM")),
            fcf_yield=self._safe_pct(km.get("fcfYieldTTM")),
            debt_equity=self._safe(ratios.get("debtEquityRatioTTM")),
            current_ratio=self._safe(ratios.get("currentRatioTTM")),
            revenue_growth=self._safe_pct(km.get("revenueGrowthTTM")),
            gross_margin=self._safe_pct(ratios.get("grossProfitMarginTTM")),
            dividend_yield=self._safe_pct(km.get("dividendYieldTTM") or km.get("dividendYieldPercentageTTM")),
            price=q.get("price", 0),
            fifty_two_week_low=q.get("yearLow"),
            fifty_two_week_high=q.get("yearHigh"),
            timestamp=datetime.utcnow().isoformat(),
            insider_ownership_pct=self._safe(insider.get("totalInsiderOwnershipPercentage")),
            shares_buyback=buyback,
            net_debt_b=net_debt,
        )

    def _safe(self, v) -> Optional[float]:
        try:
            return round(float(v), 2) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _safe_pct(self, v) -> Optional[float]:
        """Convert decimal (0.15) to percentage (15.0)"""
        try:
            f = float(v)
            return round(f * 100 if abs(f) < 2 else f, 2) if v is not None else None
        except (TypeError, ValueError):
            return None


# ─────────────────────────────────────────────
# SCORING ENGINE — region-agnostic
# ─────────────────────────────────────────────

class ValueScoringEngine:
    """
    Applies the value investing framework to a StockMetrics object.
    Scores are region-agnostic — works for US, EU, JP etc.
    Knowledge base rules live in CONFIG["filters"] and CONFIG["moat_sectors"].
    """

    def __init__(self, config: dict):
        self.f = config["filters"]
        self.moat_sectors = config["moat_sectors"]
        self.corr_groups = config["correlation_groups"]

    def score(self, m: StockMetrics) -> ScoredStock:
        failures = []
        score = 0.0

        # ── Hard filters (any failure = disqualified) ──
        if m.market_cap_b and m.market_cap_b > self.f["max_market_cap_b"]:
            failures.append(f"Market cap ${m.market_cap_b}B > ${self.f['max_market_cap_b']}B")
        if m.market_cap_b and m.market_cap_b < self.f["min_market_cap_b"]:
            failures.append(f"Market cap ${m.market_cap_b}B too small")
        if m.pe_ratio and m.pe_ratio > self.f["max_pe"] and m.pe_ratio > 0:
            failures.append(f"P/E {m.pe_ratio} > {self.f['max_pe']}")
        if m.debt_equity and m.debt_equity > self.f["max_debt_equity"]:
            failures.append(f"Debt/equity {m.debt_equity} > {self.f['max_debt_equity']}")
        if m.current_ratio and m.current_ratio < self.f["min_current_ratio"]:
            failures.append(f"Current ratio {m.current_ratio} < {self.f['min_current_ratio']}")

        # ── New quality filters ──
        # Low debt: stricter than original (0.5 vs 1.5)
        # Note: debt_equity check already above uses the updated threshold

        # Insider ownership — must have skin in the game
        if m.insider_ownership_pct is not None and m.insider_ownership_pct < self.f["min_insider_ownership"]:
            failures.append(f"Insider ownership {m.insider_ownership_pct}% < {self.f['min_insider_ownership']}%")

        # Buybacks — management must be buying back shares
        if self.f["require_buybacks"] and m.shares_buyback is False:
            failures.append("No share buybacks detected — share count not declining")

        passes = len(failures) == 0

        # ── Positive scoring (0–100) ──
        # P/E quality (25 pts)
        if m.pe_ratio and 0 < m.pe_ratio <= 10:
            score += 25
        elif m.pe_ratio and m.pe_ratio <= 15:
            score += 20
        elif m.pe_ratio and m.pe_ratio <= 20:
            score += 12

        # FCF yield (20 pts)
        if m.fcf_yield:
            if m.fcf_yield >= 8:   score += 20
            elif m.fcf_yield >= 5: score += 14
            elif m.fcf_yield >= 3: score += 8

        # ROE quality (20 pts)
        if m.roe:
            if m.roe >= 20:   score += 20
            elif m.roe >= 15: score += 14
            elif m.roe >= 10: score += 8

        # P/B (10 pts)
        if m.pb_ratio:
            if m.pb_ratio <= 1:   score += 10
            elif m.pb_ratio <= 2: score += 6
            elif m.pb_ratio <= 3: score += 3

        # Dividend yield bonus (10 pts)
        if m.dividend_yield:
            if m.dividend_yield >= 4:   score += 10
            elif m.dividend_yield >= 2: score += 6

        # Insider ownership bonus (5 pts) — Lynch: invest alongside insiders
        if m.insider_ownership_pct:
            if m.insider_ownership_pct >= 10:  score = min(score + 5, 100)
            elif m.insider_ownership_pct >= 5: score = min(score + 3, 100)
            elif m.insider_ownership_pct >= 2: score = min(score + 1, 100)

        # Buyback bonus (5 pts) — management confidence signal
        if m.shares_buyback:  score = min(score + 5, 100)

        # Net cash bonus (5 pts) — fortress balance sheet (Graham)
        if m.net_debt_b and m.net_debt_b < 0:  score = min(score + 5, 100)

        # Revenue growth (5 pts — not collapsing)
        if m.revenue_growth and m.revenue_growth >= 0:
            score += 5 if m.revenue_growth >= 5 else 3

        # Gross margin quality (10 pts)
        if m.gross_margin:
            if m.gross_margin >= 50:   score += 10
            elif m.gross_margin >= 35: score += 7
            elif m.gross_margin >= 20: score += 4

        # ── Moat score from sector ──
        moat = "unknown"
        for level, sectors in self.moat_sectors.items():
            if any(s.lower() in (m.sector + " " + m.industry).lower() for s in sectors):
                moat = level
                break
        if moat == "high":   score = min(score + 10, 100)
        elif moat == "medium": score = min(score + 5, 100)

        # ── Margin of safety (distance from 52-week high) ──
        mos = 0.0
        if m.fifty_two_week_high and m.price and m.fifty_two_week_high > 0:
            mos = round((1 - m.price / m.fifty_two_week_high) * 100, 1)

        # ── Correlation group ──
        corr_group = "Other"
        for group, keywords in self.corr_groups.items():
            if any(k.lower() in (m.sector + " " + m.industry).lower() for k in keywords):
                corr_group = group
                break

        return ScoredStock(
            metrics=m,
            passes_filters=passes,
            filter_failures=failures,
            value_score=round(score, 1),
            moat_score=moat,
            margin_of_safety=mos,
            correlation_group=corr_group,
            rationale="",  # Filled by RationaleEngine
        )


# ─────────────────────────────────────────────
# RATIONALE ENGINE — Claude API
# ─────────────────────────────────────────────

class RationaleEngine:
    """
    Generates plain-English investment rationale using Claude.
    One API call per qualifying stock — batched to control cost.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, stock: ScoredStock) -> str:
        if not self.api_key:
            return self._fallback(stock)

        m = stock.metrics
        prompt = f"""You are a value investing analyst following the frameworks of Benjamin Graham, 
Philip Fisher, Warren Buffett, Charlie Munger, Peter Lynch, Seth Klarman, and Joel Greenblatt.

Write a concise 3-sentence investment rationale for {m.ticker} ({m.company}).
Use plain language a retail investor would understand.

Key data:
- Sector: {m.sector} / {m.industry}
- Market cap: ${m.market_cap_b}B
- P/E: {m.pe_ratio}
- P/B: {m.pb_ratio}
- ROE: {m.roe}%
- FCF yield: {m.fcf_yield}%
- Debt/equity: {m.debt_equity}
- Dividend yield: {m.dividend_yield}%
- Revenue growth: {m.revenue_growth}%
- Margin of safety vs 52-week high: {stock.margin_of_safety}%
- Moat assessment: {stock.moat_score}
- Value score: {stock.value_score}/100
- Insider ownership: {m.insider_ownership_pct}%
- Share buybacks active: {m.shares_buyback}
- Net debt: ${m.net_debt_b}B (negative = net cash position)

Sentence 1: What makes this company's business model defensible (the moat).
Sentence 2: Why the current price represents value using the metrics above.
Sentence 3: One key risk to monitor.

Be direct and specific. No disclaimers needed."""

        try:
            data = json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            }).encode()

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode())
                return resp["content"][0]["text"].strip()
        except Exception as e:
            print(f"  [Claude error] {m.ticker}: {e}")
            return self._fallback(stock)

    def _fallback(self, stock: ScoredStock) -> str:
        m = stock.metrics
        return (
            f"{m.company} operates in the {m.industry} sector with a "
            f"{'wide' if stock.moat_score == 'high' else stock.moat_score} economic moat. "
            f"Trading at a P/E of {m.pe_ratio} with FCF yield of {m.fcf_yield}%, "
            f"it scores {stock.value_score}/100 on our value framework. "
            f"Monitor sector-level risks and macro sensitivity."
        )


# ─────────────────────────────────────────────
# CORRELATION FILTER — portfolio diversification
# ─────────────────────────────────────────────

def apply_correlation_filter(stocks: list[ScoredStock], max_per_group: int = 2) -> list[ScoredStock]:
    """
    Ensures no correlation group has more than max_per_group stocks.
    Within each group, keeps the highest-scoring stocks.
    """
    group_counts = {}
    filtered = []

    # Sort by score descending before filtering
    sorted_stocks = sorted(stocks, key=lambda s: s.value_score, reverse=True)

    for stock in sorted_stocks:
        group = stock.correlation_group
        count = group_counts.get(group, 0)
        if count < max_per_group:
            filtered.append(stock)
            group_counts[group] = count + 1
        else:
            print(f"  [Correlation filter] {stock.metrics.ticker} removed — {group} already has {max_per_group} stocks")

    return filtered


# ─────────────────────────────────────────────
# MAIN SCANNER ORCHESTRATOR
# ─────────────────────────────────────────────

class ValueScanner:
    """
    Main orchestrator. Runs the full pipeline:
    1. Fetch metrics from data provider
    2. Score against value framework
    3. Apply correlation filter
    4. Generate Claude rationale for qualifiers
    5. Save results to JSON

    Global expansion: pass in a different DataProvider for each region.
    The scoring engine and rationale engine work unchanged.
    """

    def __init__(self, config: dict = CONFIG):
        self.config = config
        self.data_provider =import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from data_provider_finnhub import FinnhubDataProvider
self.data_provider = FinnhubDataProvider(os.getenv("FINNHUB_API_KEY", ""))
        self.scorer = ValueScoringEngine(config)
        self.rationale_engine = RationaleEngine(config["ANTHROPIC_API_KEY"])

    def run(self, region: str = "US", top_n: int = 20) -> dict:
        print(f"\n{'='*55}")
        print(f"  ValueScan — {region} run @ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"{'='*55}\n")

        universe = self.config["universe"].get(region, [])
        print(f"Scanning {len(universe)} tickers in {region} universe...\n")

        # ── Step 1: Fetch metrics ──
        all_metrics = []
        for ticker in universe:
            m = self.data_provider.get_metrics(ticker)
            if m:
                all_metrics.append(m)
            time.sleep(0.3)  # Rate limit respect

        print(f"\n✓ Fetched {len(all_metrics)} stocks")

        # ── Step 2: Score ──
        scored = [self.scorer.score(m) for m in all_metrics]
        qualifiers = [s for s in scored if s.passes_filters]
        print(f"✓ {len(qualifiers)} stocks passed value filters")

        # ── Step 3: Correlation filter ──
        diversified = apply_correlation_filter(qualifiers)
        print(f"✓ {len(diversified)} stocks after correlation filter")

        # ── Step 4: Sort and take top N ──
        final = sorted(diversified, key=lambda s: s.value_score, reverse=True)[:top_n]

        # ── Step 5: Generate rationale for qualifiers ──
        print(f"\nGenerating AI rationale for {len(final)} stocks...")
        for i, stock in enumerate(final):
            print(f"  [{i+1}/{len(final)}] {stock.metrics.ticker}")
            stock.rationale = self.rationale_engine.generate(stock)
            time.sleep(0.5)

        # ── Step 6: Save results ──
        result = {
            "scan_date": date.today().isoformat(),
            "scan_time_utc": datetime.utcnow().isoformat(),
            "region": region,
            "universe_size": len(universe),
            "stocks_fetched": len(all_metrics),
            "stocks_qualified": len(qualifiers),
            "stocks_after_correlation": len(diversified),
            "top_picks": [self._serialize(s) for s in final],
            "all_scored": [self._serialize(s) for s in sorted(scored, key=lambda x: x.value_score, reverse=True)],
        }

        out_path = f"output/scan_{region}_{date.today().isoformat()}.json"
        os.makedirs("output", exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"\n✓ Results saved → {out_path}")
        self._print_summary(final)
        return result

    def _serialize(self, s: ScoredStock) -> dict:
        d = asdict(s)
        return d

    def _print_summary(self, stocks: list[ScoredStock]):
        print(f"\n{'─'*55}")
        print(f"  TOP PICKS — ValueScan US")
        print(f"{'─'*55}")
        print(f"  {'#':<3} {'Ticker':<7} {'Score':>6} {'P/E':>6} {'FCF%':>6} {'MoS%':>6}  Company")
        print(f"  {'─'*52}")
        for i, s in enumerate(stocks, 1):
            m = s.metrics
            insider_str = f"{m.insider_ownership_pct:.1f}%" if m.insider_ownership_pct else "-"
            buyback_str = "Yes" if m.shares_buyback else ("No" if m.shares_buyback is False else "-")
            print(
                f"  {i:<3} {m.ticker:<7} {s.value_score:>5.1f}  "
                f"{str(m.pe_ratio or '-'):>5}  "
                f"{str(m.fcf_yield or '-'):>5}  "
                f"{s.margin_of_safety:>5.1f}%  "
                f"Ins:{insider_str}  Buy:{buyback_str}  {m.company}"
            )
        print(f"{'─'*55}\n")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    scanner = ValueScanner()
    scanner.run(region="US", top_n=20)

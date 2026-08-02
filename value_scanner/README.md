# ValueScan — US Value Stock Scanner
### Built on Graham · Fisher · Buffett/Munger · Lynch · Klarman · Greenblatt

A production-ready stock scanner that runs the value investing framework
automatically each trading day, scores every stock, filters for portfolio
correlation, and generates AI rationale via Claude.

---

## Quick start (5 minutes)

### 1. Install dependencies
```bash
pip install requests  # only needed if you swap urllib for requests
# No other dependencies — pure Python stdlib
```

### 2. Set your API keys
```bash
export FMP_API_KEY="your_key_here"        # financialmodelingprep.com — free tier works
export ANTHROPIC_API_KEY="your_key_here"  # console.anthropic.com
```

### 3. Run a scan
```bash
cd scanner
python scanner.py
```

Results are saved to `output/scan_US_YYYY-MM-DD.json`

### 4. View the dashboard
Open `dashboard/index.html` in a browser.
It reads the latest JSON from `output/` automatically.

---

## Architecture

```
value_scanner/
├── scanner/
│   ├── scanner.py       ← Main engine (data → score → rationale)
│   └── scheduler.py     ← Daily auto-run at 4:30 PM ET
├── dashboard/
│   └── index.html       ← Subscriber-facing web dashboard
├── output/
│   └── scan_US_*.json   ← Daily results (one file per scan)
└── README.md
```

### How a scan run works

1. **Fetch** — `USDataProvider` pulls quote + fundamentals for every ticker
   in `CONFIG["universe"]["US"]` from Financial Modeling Prep API
2. **Score** — `ValueScoringEngine` applies 9 Graham/Fisher filters and
   assigns a 0–100 value score based on P/E, FCF yield, ROE, P/B, moat sector
3. **Filter** — stocks that fail any hard filter are removed
4. **Correlate** — `apply_correlation_filter` enforces max 2 stocks per
   sector group so picks don't all move together
5. **Rationale** — `RationaleEngine` calls Claude to write a 3-sentence
   plain-English rationale for every qualifying stock
6. **Save** — full results written to `output/scan_US_YYYY-MM-DD.json`

---

## Configuration

All settings live in `scanner.py → CONFIG`:

| Key | What it controls |
|-----|-----------------|
| `filters.max_pe` | Maximum P/E to pass (default 20) |
| `filters.max_market_cap_b` | Max market cap in $B (default 100) |
| `filters.min_fcf_yield` | Minimum FCF yield % (default 3) |
| `moat_sectors` | Which sectors map to wide/narrow moat |
| `correlation_groups` | Sector groups for diversification filter |
| `universe.US` | List of tickers to scan |

---

## Adding tickers to the universe

Edit `CONFIG["universe"]["US"]` in `scanner.py`:
```python
"universe": {
    "US": [
        "STZ", "BMY", "HII", ...  # add any US ticker here
    ],
}
```

---

## Global expansion (pre-wired)

The architecture is designed for global markets from day one.
When ready, uncomment in `CONFIG["universe"]`:

```python
"EU": ["SAP.XETRA", "RELX.LSE", "DGEX.LSE"],   # Europe — EODHD format
"JP": ["7203.TSE", "6501.TSE"],                  # Japan — TSE format
"KR": ["005930.KSC"],                            # Korea — KOSPI format
```

Then create a new data provider class:
```python
class EUDataProvider:
    """Same interface as USDataProvider but uses EODHD API."""
    def get_metrics(self, ticker: str) -> Optional[StockMetrics]:
        # EODHD API call — returns same StockMetrics dataclass
        # Currency normalisation: convert EUR/GBP/JPY to USD automatically
        pass
```

The scoring engine and rationale engine work unchanged for any region.
Add 3 scan windows for global (JP close 6 AM UTC, EU close 5 PM UTC, US close 10 PM UTC).

---

## Running continuously (production)

```bash
# Run once daily at market close
python scheduler.py

# Or with cron (recommended for servers)
# 30 16 * * 1-5  cd /path/to/scanner && python scanner.py
```

---

## API costs (monthly estimate)

| Service | Usage | Cost |
|---------|-------|------|
| Financial Modeling Prep | ~60 tickers × 3 API calls × 22 days | Free tier or ~$30/mo |
| Anthropic Claude | ~20 rationales × 200 tokens × 22 days | ~$2–5/mo |
| Hosting (Vercel/Railway) | Static dashboard + JSON | Free tier |
| **Total** | | **~$35/mo** |

At 100 subscribers × $19/mo = $1,900 revenue vs ~$35 costs = 98% gross margin.

---

## Legal disclaimer

This software is for educational and research purposes only.
It does not constitute personalised investment advice.
Always add a clear disclaimer on any subscriber-facing product.
Consult a securities attorney before launching a paid service.

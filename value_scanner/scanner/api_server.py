"""
ValueScan API Server
Serves live scan results to the website and triggers automated emails via Beehiiv.

Deploy on Render.com ($7/month) alongside the scanner.

Endpoints:
  GET  /api/picks          — latest top picks (used by website)
  GET  /api/picks/free     — top 3 only (free tier)
  GET  /api/gems           — hidden gems list
  GET  /api/research       — research articles list
  GET  /api/status         — last scan time and health
  POST /api/trigger-scan   — manually trigger a scan (secured)
  POST /api/webhook/scan-complete — called by scanner when done, triggers email
"""

import json
import os
import glob
import hmac
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread


# ─── CONFIG ───────────────────────────────────
PORT             = int(os.getenv("PORT", 8080))
OUTPUT_DIR       = os.getenv("OUTPUT_DIR", "./output")
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "change-this-secret")
RESEND_API_KEY   = os.getenv("RESEND_API_KEY", "")    # from resend.com
FROM_EMAIL       = os.getenv("FROM_EMAIL", "hello@scansvalue.com")
ANTHROPIC_API_KEY= os.getenv("ANTHROPIC_API_KEY", "")


# ─── HELPERS ──────────────────────────────────

def load_latest_scan():
    """Load the most recent scan JSON from output directory."""
    files = sorted(glob.glob(f"{OUTPUT_DIR}/scan_US_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0]) as f:
        return json.load(f)

def cors_headers():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }

def json_response(data):
    return json.dumps(data, indent=2).encode()



def load_subscribers():
    """Load subscriber email list from file."""
    sub_file = os.path.join(OUTPUT_DIR, "subscribers.json")
    if os.path.exists(sub_file):
        with open(sub_file) as f:
            return json.load(f)
    return []

def save_subscriber(email, name="", plan="free"):
    """Add a new subscriber to the list."""
    subscribers = load_subscribers()
    # Avoid duplicates
    if not any(s.get("email") == email for s in subscribers):
        subscribers.append({"email": email, "name": name, "plan": plan, "joined": date.today().isoformat()})
        sub_file = os.path.join(OUTPUT_DIR, "subscribers.json")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(sub_file, "w") as f:
            json.dump(subscribers, f, indent=2)
        print(f"  [Subscribers] Added {email} ({plan})")


# ─── EMAIL GENERATION ─────────────────────────

def generate_email_html(picks, scan_date):
    """Generate the HTML email body from top picks."""
    picks_html = ""
    for i, pick in enumerate(picks[:3], 1):
        m = pick["metrics"]
        rationale = pick.get("rationale", "Research available in dashboard.")
        gem_score = pick.get("value_score", 0)
        moat = pick.get("moat_score", "unknown")
        mos = pick.get("margin_of_safety", 0)

        picks_html += f"""
        <div style="background:#1E3120;border:1px solid #2E4A30;border-left:3px solid #D4A83A;
                    border-radius:0 10px 10px 0;padding:24px;margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
            <div>
              <div style="font-family:monospace;font-size:18px;font-weight:600;color:#F2ECD8">
                {m['ticker']}
              </div>
              <div style="font-size:13px;color:#8FA882;margin-top:2px">{m['company']}</div>
            </div>
            <div style="background:rgba(212,168,58,.15);border:1px solid rgba(212,168,58,.3);
                        border-radius:8px;padding:6px 12px;text-align:center;">
              <div style="font-size:10px;color:#D4A83A;text-transform:uppercase;
                          letter-spacing:.06em;margin-bottom:2px">Hidden Gem Score</div>
              <div style="font-size:20px;font-weight:600;color:#D4A83A">{gem_score}</div>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px;">
            <div style="background:rgba(0,0,0,.2);border-radius:6px;padding:8px;">
              <div style="font-size:9px;color:#8FA882;text-transform:uppercase;letter-spacing:.06em">Moat</div>
              <div style="font-size:13px;color:#F2ECD8;font-weight:500;text-transform:capitalize">{moat}</div>
            </div>
            <div style="background:rgba(0,0,0,.2);border-radius:6px;padding:8px;">
              <div style="font-size:9px;color:#8FA882;text-transform:uppercase;letter-spacing:.06em">Margin of safety</div>
              <div style="font-size:13px;color:#F2ECD8;font-weight:500">{mos}% below high</div>
            </div>
            <div style="background:rgba(0,0,0,.2);border-radius:6px;padding:8px;">
              <div style="font-size:9px;color:#8FA882;text-transform:uppercase;letter-spacing:.06em">Sector</div>
              <div style="font-size:13px;color:#F2ECD8;font-weight:500">{m.get('sector','—')}</div>
            </div>
          </div>

          <div style="font-size:14px;color:#C8D8C0;line-height:1.7;margin-bottom:14px;">
            {rationale}
          </div>

          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            {"" if not m.get("insider_ownership_pct") else
             f'<span style="font-size:11px;padding:3px 8px;background:rgba(212,168,58,.1);color:#D4A83A;border:1px solid rgba(212,168,58,.2);border-radius:6px">Insider ownership: {m.get("insider_ownership_pct",0):.1f}%</span>'}
            {"" if not m.get("shares_buyback") else
             '<span style="font-size:11px;padding:3px 8px;background:rgba(212,168,58,.1);color:#D4A83A;border:1px solid rgba(212,168,58,.2);border-radius:6px">Active buybacks ✓</span>'}
            {"" if not m.get("dividend_yield") else
             f'<span style="font-size:11px;padding:3px 8px;background:rgba(0,0,0,.2);color:#8FA882;border:1px solid #2E4A30;border-radius:6px">Dividend: {m.get("dividend_yield",0):.1f}%</span>'}
          </div>
        </div>
        """

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#111E11;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:20px;">

    <!-- Header -->
    <div style="text-align:center;padding:32px 0 24px;border-bottom:1px solid #2E4A30;margin-bottom:28px;">
      <div style="font-size:11px;font-family:monospace;color:#D4A83A;letter-spacing:.12em;
                  text-transform:uppercase;margin-bottom:8px">Daily value research</div>
      <div style="font-family:Georgia,serif;font-size:36px;font-weight:600;color:#F2ECD8;
                  letter-spacing:-.01em">ValueScan</div>
      <div style="font-size:12px;color:#4A6B4A;margin-top:6px;font-family:monospace">{scan_date}</div>
    </div>

    <!-- Intro -->
    <div style="margin-bottom:24px;">
      <div style="font-family:Georgia,serif;font-size:20px;font-weight:600;color:#F2ECD8;
                  margin-bottom:8px">Today's top picks from the daily scan</div>
      <div style="font-size:14px;color:#8FA882;line-height:1.6;">
        These stocks passed all 12 criteria today — low debt, active buybacks, insider ownership,
        durable moat, and price below intrinsic value. Here is the research.
      </div>
    </div>

    <!-- Picks -->
    {picks_html}

    <!-- Disclaimer -->
    <div style="margin-top:28px;padding:16px;background:#1A2E1A;border:1px solid #2E4A30;
                border-radius:8px;font-size:11px;color:#4A6B4A;line-height:1.6;">
      ValueScan is an educational research service, not a registered investment adviser.
      This email does not constitute personalised financial advice or a recommendation to
      buy or sell any security. Always conduct your own due diligence.
      <a href="https://scansvalue.com/disclaimer" style="color:#8FA882">Full disclaimer →</a>
    </div>

    <!-- Footer -->
    <div style="text-align:center;padding:24px 0;font-size:11px;color:#4A6B4A;">
      <a href="https://scansvalue.com" style="color:#8FA882;text-decoration:none">scansvalue.com</a>
      &nbsp;·&nbsp;
      <a href="{{{{ unsubscribe_url }}}}" style="color:#4A6B4A;text-decoration:none">Unsubscribe</a>
    </div>

  </div>
</body>
</html>
"""


def generate_email_subject(picks, scan_date):
    """Generate email subject line from top picks."""
    if not picks:
        return f"ValueScan — Daily research {scan_date}"
    top = picks[0]["metrics"]
    score = picks[0].get("value_score", 0)
    return f"ValueScan — {top['ticker']} leads today's research (score: {score:.0f}/100)"


# ─── BEEHIIV INTEGRATION ──────────────────────

def send_email_via_resend(to_emails, picks, scan_date):
    """
    Send daily research email via Resend API.
    Resend docs: https://resend.com/docs/api-reference/emails/send-email
    to_emails: list of subscriber email addresses
    """
    if not RESEND_API_KEY:
        print("  [Email] Resend not configured — set RESEND_API_KEY env var")
        return False

    html_body = generate_email_html(picks, scan_date)
    subject   = generate_email_subject(picks, scan_date)

    # Resend supports batch sending — send to all subscribers at once
    # For large lists use the batch endpoint
    payload = json.dumps({
        "from":    f"ValueScan <{FROM_EMAIL}>",
        "to":      to_emails if isinstance(to_emails, list) else [to_emails],
        "subject": subject,
        "html":    html_body,
        "reply_to": FROM_EMAIL,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {RESEND_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
            email_id = result.get("id")
            print(f"  [Email] Sent via Resend — id: {email_id}")
            return True
    except Exception as e:
        print(f"  [Email] Resend error: {e}")
        return False


def send_welcome_email_resend(to_email, first_name, picks, scan_date):
    """Send instant welcome email with top 3 picks to new subscriber."""
    if not RESEND_API_KEY:
        return False

    html_body = f"""
    <div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;background:#111E11;padding:20px;">
      <h2 style="color:#D4A83A;font-size:28px">Welcome to ValueScan{', ' + first_name if first_name else ''}.</h2>
      <p style="color:#8FA882;font-size:14px;line-height:1.7;margin-bottom:20px">
        Here are the 3 highest-scoring stocks from today's research.
        Every trading day at 5 PM ET, the full daily report lands in your inbox.
      </p>
    """ + generate_email_html(picks, scan_date) + "</div>"

    payload = json.dumps({
        "from":    f"ValueScan <{FROM_EMAIL}>",
        "to":      [to_email],
        "subject": "Welcome to ValueScan — your first 3 research reports",
        "html":    html_body,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {RESEND_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
            print(f"  [Welcome] Sent to {to_email} — id: {result.get('id')}")
            return True
    except Exception as e:
        print(f"  [Welcome] Error: {e}")
        return False


# ─── WELCOME EMAIL ────────────────────────────

def send_welcome_email(subscriber_email, first_name):
    """
    Send instant welcome email with top 3 picks to a new subscriber.
    Called when Beehiiv webhook fires on new subscription.
    """
    scan = load_latest_scan()
    if not scan:
        print(f"  [Welcome] No scan data available for {subscriber_email}")
        return False

    picks = scan.get("top_picks", [])[:3]
    scan_date = scan.get("scan_date", date.today().isoformat())

    html_body = f"""
    <p style="font-family:Georgia,serif;font-size:18px;color:#F2ECD8">
      Welcome to ValueScan{', ' + first_name if first_name else ''}.
    </p>
    <p style="font-size:14px;color:#8FA882;line-height:1.6;margin-bottom:24px;">
      Here are the 3 highest-scoring stocks from today's research — your first look at
      what the daily scan found. Every evening after market close, the full report
      lands in your inbox.
    </p>
    """ + generate_email_html(picks, scan_date)

    # Use Beehiiv's subscriber-specific send
    # In practice, new subscriber emails are handled by Beehiiv's
    # automation flow — set this up in Beehiiv dashboard under
    # Automations → New subscriber → Send email
    print(f"  [Welcome] Would send welcome to {subscriber_email} with {len(picks)} picks")
    print(f"  [Welcome] Configure in Beehiiv: Automations → Welcome email → use template")
    return True


# ─── HTTP REQUEST HANDLER ─────────────────────

class ValueScanAPI(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  [API] {self.address_string()} {format % args}")

    def send_json(self, data, status=200):
        body = json_response(data)
        self.send_response(status)
        for k, v in cors_headers().items():
            self.send_header(k, v)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        for k, v in cors_headers().items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/picks":
            self._get_picks(limit=None)
        elif path == "/api/picks/free":
            self._get_picks(limit=3)
        elif path == "/api/gems":
            self._get_gems()
        elif path == "/api/research":
            self._get_research()
        elif path == "/api/status":
            self._get_status()
        elif path == "/api/subscribers/count":
            subs = load_subscribers()
            self.send_json({"count": len(subs), "subscribers": [s["email"] for s in subs]})
        elif path.startswith("/api/scan/"):
            ticker = path.split("/api/scan/")[-1].strip("/")
            self._scan_single_ticker(ticker)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if path == "/api/webhook/scan-complete":
            self._handle_scan_complete(body)
        elif path == "/api/webhook/new-subscriber":
            self._handle_new_subscriber(body)
        else:
            self.send_json({"error": "Not found"}, 404)

    # ── Endpoints ──

    def _get_picks(self, limit=None):
        scan = load_latest_scan()
        if not scan:
            self.send_json({"error": "No scan data available", "picks": []})
            return

        picks = scan.get("top_picks", [])
        if limit:
            picks = picks[:limit]

        # Simplify for frontend — only what the website needs
        simplified = []
        for p in picks:
            m = p["metrics"]
            simplified.append({
                "ticker":      m["ticker"],
                "company":     m["company"],
                "sector":      m["sector"],
                "gem_score":   round(p.get("value_score", 0), 1),
                "moat":        p.get("moat_score", "unknown"),
                "mos_pct":     p.get("margin_of_safety", 0),
                "rationale":   p.get("rationale", ""),
                "buybacks":    m.get("shares_buyback", False),
                "insider_pct": m.get("insider_ownership_pct"),
                "dividend":    m.get("dividend_yield"),
                "net_debt_b":  m.get("net_debt_b"),
                "buffett": {
                    "moat":       min(10, round(p.get("value_score", 0) / 10)),
                    "valuation":  min(10, round((100 - (m.get("pe_ratio") or 20)) / 8)),
                    "mgmt":       8 if m.get("shares_buyback") else 6,
                    "predictability": 7,
                },
                "graham": {
                    "earnings_stable": (m.get("revenue_growth") or 0) > -5,
                    "below_intrinsic": p.get("margin_of_safety", 0) > 10,
                    "balance_sheet":   (m.get("current_ratio") or 0) > 1.2,
                }
            })

        self.send_json({
            "scan_date":   scan.get("scan_date"),
            "scan_time":   scan.get("scan_time_utc"),
            "total_picks": len(scan.get("top_picks", [])),
            "picks":       simplified,
        })

    def _get_gems(self):
        """Return hidden gems — smaller companies with high scores."""
        scan = load_latest_scan()
        if not scan:
            self.send_json({"gems": []})
            return

        all_scored = scan.get("all_scored", scan.get("top_picks", []))
        gems = []
        for p in sorted(all_scored, key=lambda x: x.get("value_score", 0), reverse=True):
            m = p["metrics"]
            cap = m.get("market_cap_b", 999)
            if cap and cap < 25:  # Sub-$25B — hidden gems territory
                gems.append({
                    "ticker":    m["ticker"],
                    "company":   m["company"],
                    "sector":    m["sector"],
                    "gem_score": round(p.get("value_score", 0), 1),
                    "market_cap_b": cap,
                })
                if len(gems) >= 8:
                    break

        self.send_json({"gems": gems, "scan_date": scan.get("scan_date")})

    def _get_research(self):
        """
        Return research articles.
        In full build: fetch from a CMS or database.
        Currently returns the scanner's rationale as article excerpts.
        """
        scan = load_latest_scan()
        articles = []

        if scan:
            for p in scan.get("top_picks", [])[:6]:
                m = p["metrics"]
                rationale = p.get("rationale", "")
                if rationale:
                    articles.append({
                        "ticker":   m["ticker"],
                        "company":  m["company"],
                        "sector":   m["sector"],
                        "title":    f"Is {m['company']} undervalued right now?",
                        "excerpt":  rationale[:180] + "..." if len(rationale) > 180 else rationale,
                        "date":     scan.get("scan_date", ""),
                        "category": "deep-value",
                        "read_min": 5,
                    })

        self.send_json({"articles": articles, "scan_date": scan.get("scan_date") if scan else None})

    def _get_status(self):
        scan = load_latest_scan()
        self.send_json({
            "status":       "ok",
            "last_scan":    scan.get("scan_date") if scan else None,
            "last_scan_utc":scan.get("scan_time_utc") if scan else None,
            "picks_count":  len(scan.get("top_picks", [])) if scan else 0,
            "server_time":  datetime.utcnow().isoformat(),
            "resend_configured": bool(RESEND_API_KEY),
        })

    def _handle_scan_complete(self, body):
        """
        Called by scanner.py after each successful scan.
        Triggers email delivery to all subscribers.
        """
        # Verify webhook secret
        sig = self.headers.get("X-Webhook-Secret", "")
        if sig != WEBHOOK_SECRET:
            self.send_json({"error": "Unauthorized"}, 401)
            return

        scan = load_latest_scan()
        if not scan:
            self.send_json({"error": "No scan data"}, 500)
            return

        picks     = scan.get("top_picks", [])
        scan_date = scan.get("scan_date", date.today().isoformat())

        print(f"\n[Webhook] Scan complete — sending email for {scan_date}")
        print(f"[Webhook] {len(picks)} picks to include")

        # Run email send in background thread so API responds immediately
        # Load subscriber list from file (add emails as subscribers sign up)
        subscribers = load_subscribers()
        Thread(target=send_email_via_resend, args=(subscribers, picks, scan_date), daemon=True).start()

        self.send_json({
            "status":     "email_queued",
            "picks":      len(picks),
            "scan_date":  scan_date,
        })

    def _handle_new_subscriber(self, body):
        """
        Called by Beehiiv webhook when someone new subscribes.
        Sends them the top 3 picks immediately.
        """
        try:
            data  = json.loads(body)
            email = data.get("email", "")
            name  = data.get("first_name", "")
            print(f"[Webhook] New subscriber: {email}")
            save_subscriber(email, name, data.get("plan", "free"))
            scan = load_latest_scan()
            picks = scan.get("top_picks", [])[:3] if scan else []
            scan_date = scan.get("scan_date", "") if scan else ""
            Thread(target=send_welcome_email_resend, args=(email, name, picks, scan_date), daemon=True).start()
            self.send_json({"status": "welcome_email_queued"})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)



    def _scan_single_ticker(self, ticker):
        """
        Fetch live data for one ticker, score it, generate AI analysis.
        Called when a paid subscriber enters a ticker in the scanner tool.
        """
        ticker = ticker.upper().strip()
        if not ticker or len(ticker) > 6:
            self.send_json({"error": "Invalid ticker"}, 400)
            return

        print(f"  [Scan] On-demand scan for {ticker}")

        # 1. Fetch metrics using existing data provider
        fmp_key = os.getenv("FMP_API_KEY", "demo")
        provider = __import__('scanner').USDataProvider(fmp_key)

        metrics = provider.get_metrics(ticker)
        if not metrics:
            self.send_json({"error": f"Could not fetch data for {ticker}. Check the ticker is valid."}, 404)
            return

        # 2. Score it
        from scanner import ValueScoringEngine, CONFIG
        scorer = ValueScoringEngine(CONFIG)
        scored = scorer.score(metrics)

        # 3. Build criteria list
        criteria = [
            {"label": "P/E below 20",          "pass": bool(metrics.pe_ratio and 0 < metrics.pe_ratio <= 20),     "value": f"{metrics.pe_ratio}x" if metrics.pe_ratio else "—"},
            {"label": "Low debt (D/E < 0.5)",   "pass": bool(metrics.debt_equity and metrics.debt_equity <= 0.5),  "value": f"{metrics.debt_equity}x" if metrics.debt_equity else "—"},
            {"label": "FCF yield > 3%",         "pass": bool(metrics.fcf_yield and metrics.fcf_yield >= 3),        "value": f"{metrics.fcf_yield}%" if metrics.fcf_yield else "—"},
            {"label": "Insider ownership > 2%", "pass": bool(metrics.insider_ownership_pct and metrics.insider_ownership_pct >= 2), "value": f"{metrics.insider_ownership_pct}%" if metrics.insider_ownership_pct else "—"},
            {"label": "Active buybacks",        "pass": bool(metrics.shares_buyback),                               "value": "Yes ✓" if metrics.shares_buyback else "None"},
            {"label": "Current ratio > 1.2",    "pass": bool(metrics.current_ratio and metrics.current_ratio >= 1.2), "value": f"{metrics.current_ratio}" if metrics.current_ratio else "—"},
            {"label": "Market cap < $100B",     "pass": bool(metrics.market_cap_b and metrics.market_cap_b <= 100), "value": f"${metrics.market_cap_b}B" if metrics.market_cap_b else "—"},
            {"label": "ROE > 10%",             "pass": bool(metrics.roe and metrics.roe >= 10),                    "value": f"{metrics.roe}%" if metrics.roe else "—"},
            {"label": "Revenue not falling",    "pass": bool(metrics.revenue_growth and metrics.revenue_growth >= -5), "value": f"{metrics.revenue_growth}%" if metrics.revenue_growth else "—"},
            {"label": "Gross margin > 20%",    "pass": bool(metrics.gross_margin and metrics.gross_margin >= 20),   "value": f"{metrics.gross_margin}%" if metrics.gross_margin else "—"},
            {"label": "Margin of safety > 10%","pass": scored.margin_of_safety >= 10,                              "value": f"{scored.margin_of_safety}%"},
            {"label": "Wide or narrow moat",   "pass": scored.moat_score in ("high", "medium"),                    "value": scored.moat_score.title()},
        ]

        graham_passes = sum(1 for c in criteria[:5] if c["pass"])
        buffett_score = round(min(10, scored.value_score / 10), 1)

        # 4. Generate AI analysis
        ant_key = os.getenv("ANTHROPIC_API_KEY", "")
        rationale_engine = __import__('scanner').RationaleEngine(ant_key)

        # Extended prompt for on-demand scanner
        m = metrics
        prompt = f"""You are a senior value investing analyst using the frameworks of Graham, Buffett, Munger, Lynch, and Klarman.

    Analyse {m.ticker} ({m.company}) as a potential value investment.

    Data:
    - Sector: {m.sector} / {m.industry}
    - Market cap: ${m.market_cap_b}B
    - P/E: {m.pe_ratio} | P/B: {m.pb_ratio} | ROE: {m.roe}%
    - FCF yield: {m.fcf_yield}% | Debt/equity: {m.debt_equity}
    - Revenue growth: {m.revenue_growth}% | Gross margin: {m.gross_margin}%
    - Dividend yield: {m.dividend_yield}% | Insider ownership: {m.insider_ownership_pct}%
    - Active buybacks: {m.shares_buyback} | Net debt: ${m.net_debt_b}B
    - Margin of safety vs 52-week high: {scored.margin_of_safety}%
    - Moat assessment: {scored.moat_score} | Value score: {scored.value_score}/100

    Write a JSON response with exactly these fields:
    {{
      "writeup": "3-sentence plain-English analysis of the business and why it is or is not a value investment right now",
      "bull_case": "1-sentence bull case — what would have to be true for this to perform well",
      "bear_case": "1-sentence bear case — what could go wrong",
      "verdict": "1-sentence bottom line — value, watchlist, or avoid, and why",
      "risk": "The single most important risk to monitor"
    }}

    Be direct. Use plain language. No disclaimers."""

        analysis = {"writeup": "", "bull_case": "", "bear_case": "", "verdict": "", "risk": ""}
        if ant_key:
            try:
                import json as _json
                payload = _json.dumps({
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}]
                }).encode()
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=payload,
                    headers={"Content-Type":"application/json","x-api-key":ant_key,"anthropic-version":"2023-06-01"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    resp = _json.loads(r.read().decode())
                    raw = resp["content"][0]["text"].strip()
                    # Strip markdown fences if present
                    raw = raw.replace("```json","").replace("```","").strip()
                    analysis = _json.loads(raw)
            except Exception as e:
                print(f"  [Analysis] Error: {e}")
                analysis["writeup"] = f"{m.company} is a {m.sector} company with a {scored.moat_score} competitive moat. It scores {scored.value_score}/100 on our value framework with a {scored.margin_of_safety}% margin of safety."
                analysis["verdict"] = "Pass" if scored.passes_filters else f"Does not pass: {', '.join(scored.filter_failures[:2])}"

        # 5. Return full result
        self.send_json({
            "ticker": ticker,
            "metrics": {
                "company":    m.company,
                "sector":     m.sector,
                "industry":   m.industry,
                "pe_ratio":   m.pe_ratio,
                "fcf_yield":  m.fcf_yield,
                "debt_equity":m.debt_equity,
                "roe":        m.roe,
                "div_yield":  m.dividend_yield,
                "market_cap_b": m.market_cap_b,
                "insider_pct": m.insider_ownership_pct,
                "buybacks":   m.shares_buyback,
                "gross_margin": m.gross_margin,
                "revenue_growth": m.revenue_growth,
            },
            "scores": {
                "gem_score":     round(scored.value_score, 1),
                "buffett_score": buffett_score,
                "mos_pct":       scored.margin_of_safety,
                "moat":          scored.moat_score,
                "passes_all":    scored.passes_filters,
                "filter_failures": scored.filter_failures,
                "graham_passes": graham_passes,
                "criteria":      criteria,
            },
            "analysis": analysis,
        })


# ─── ENTRY POINT ──────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"\nValueScan API server starting on port {PORT}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Beehiiv configured: {bool(BEEHIIV_API_KEY and BEEHIIV_PUB_ID)}")
    print(f"\nEndpoints:")
    print(f"  GET  /api/picks          — full picks list")
    print(f"  GET  /api/picks/free     — top 3 only")
    print(f"  GET  /api/gems           — hidden gems")
    print(f"  GET  /api/research       — research articles")
    print(f"  GET  /api/status         — health check")
    print(f"  POST /api/webhook/scan-complete    — triggered by scanner")
    print(f"  POST /api/webhook/new-subscriber   — triggered by Beehiiv")
    print(f"\nRunning...\n")

    server = HTTPServer(("0.0.0.0", PORT), ValueScanAPI)
    server.serve_forever()


# ─── ON-DEMAND STOCK SCANNER ──────────────────────────────────
# Add this method to ValueScanAPI class and add routing in do_GET

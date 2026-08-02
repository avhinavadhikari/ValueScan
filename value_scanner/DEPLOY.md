# ValueScan — Automated System Deployment Guide
# Estimated time: 45 minutes
# Monthly cost: $7 (Render.com server) + $3 (API costs) = $10/month total

═══════════════════════════════════════════════════════
OVERVIEW — HOW THE AUTOMATION WORKS
═══════════════════════════════════════════════════════

Every trading day this is what happens automatically:

4:30 PM ET  →  scanner.py fetches live data for 56 stocks
4:35 PM ET  →  Scoring engine applies 12 value criteria
4:40 PM ET  →  Results saved to scan_US_YYYY-MM-DD.json
4:41 PM ET  →  scanner calls /api/webhook/scan-complete
4:42 PM ET  →  api_server.py generates email HTML from results
4:43 PM ET  →  Beehiiv API sends email to all subscribers
4:45 PM ET  →  Website at scansvalue.com shows new picks live

New subscriber signs up  →  Beehiiv webhook calls /api/webhook/new-subscriber
                         →  Welcome email sends with today's top 3 picks


═══════════════════════════════════════════════════════
STEP 1 — SIGN UP FOR REQUIRED ACCOUNTS (15 min)
═══════════════════════════════════════════════════════

You need 3 accounts if you don't have them:

A) Financial Modeling Prep (market data)
   → https://financialmodelingprep.com
   → Sign up free (250 API calls/day — enough to start)
   → Copy your API key from the dashboard

B) Anthropic (research write-ups)
   → https://console.anthropic.com
   → Add $10 credit — will last months at current usage
   → Copy your API key

C) Beehiiv (email delivery)
   → https://www.beehiiv.com
   → Create free account
   → Create a publication called "ValueScan"
   → Go to Settings → API → copy your API key
   → Note your Publication ID (starts with pub_)

D) Render.com (server hosting)
   → https://render.com
   → Sign up free (we'll use $7/month plan)


═══════════════════════════════════════════════════════
STEP 2 — DEPLOY ON RENDER.COM (15 min)
═══════════════════════════════════════════════════════

1. Go to render.com → New → Web Service
2. Choose "Deploy from a Git repo" OR "Upload files"

IF UPLOADING FILES (easier):
   - Zip the entire value_scanner/ folder
   - Upload the zip
   - Set start command to: python api_server.py

IF USING GITHUB (recommended for updates):
   - Push your code to a GitHub repo
   - Connect Render to that repo
   - Set start command: python api_server.py

3. Set these environment variables in Render dashboard:
   (Settings → Environment → Add Environment Variable)

   FMP_API_KEY        = your FMP key from step 1A
   ANTHROPIC_API_KEY  = your Anthropic key from step 1B
   BEEHIIV_API_KEY    = your Beehiiv key from step 1C
   BEEHIIV_PUB_ID     = your pub_xxxxxxxx from Beehiiv
   WEBHOOK_SECRET     = make up a long random string (e.g. vs-secret-2026-xyz)
   API_URL            = https://your-app.onrender.com (fill after deploy)
   OUTPUT_DIR         = ./output
   PORT               = 8080

4. Click Deploy — Render builds and starts the server
5. Copy your live URL (e.g. https://valuescan-api.onrender.com)

6. Test it: open https://your-url.onrender.com/api/status
   You should see: {"status":"ok","beehiiv_configured":true,...}


═══════════════════════════════════════════════════════
STEP 3 — ADD SCHEDULER ON RENDER (5 min)
═══════════════════════════════════════════════════════

The scanner needs to run every trading day at 4:30 PM ET.

In Render dashboard:
1. New → Cron Job
2. Command: python scanner/scanner.py
3. Schedule: 30 20 * * 1-5
   (That's 8:30 PM UTC = 4:30 PM ET, Mon-Fri)
4. Add the same environment variables as above
5. Save

This replaces scheduler.py — Render handles the timing.


═══════════════════════════════════════════════════════
STEP 4 — UPDATE THE WEBSITE (5 min)
═══════════════════════════════════════════════════════

In your index.html file, find this line near the top of the script:

   const API_URL = 'https://your-api.onrender.com';

Replace it with your actual Render URL:

   const API_URL = 'https://valuescan-api.onrender.com';

Save and re-upload to Vercel as index.html.

Now the website fetches live data from your API every time someone visits.


═══════════════════════════════════════════════════════
STEP 5 — SET UP BEEHIIV EMAIL AUTOMATION (10 min)
═══════════════════════════════════════════════════════

A) Welcome email for new subscribers:
   → Beehiiv dashboard → Automations → Create Automation
   → Trigger: New subscriber joins
   → Action: Send email
   → Subject: "Your 3 ValueScan research reports are inside"
   → Content: Use the welcome email template (api_server.py generates this)

B) Connect Beehiiv webhook for instant welcome email:
   → Beehiiv → Settings → Integrations → Webhooks
   → Add webhook URL: https://your-api.onrender.com/api/webhook/new-subscriber
   → Event: subscriber.created
   → Save

C) Test the full flow:
   → Go to scansvalue.com
   → Sign up with your own email
   → Check your inbox — report should arrive within 2-3 minutes
   → If it doesn't, check Render logs for errors


═══════════════════════════════════════════════════════
STEP 6 — TEST A MANUAL SCAN (5 min)
═══════════════════════════════════════════════════════

Run a test scan to confirm everything works:

   curl -X POST https://your-api.onrender.com/api/webhook/scan-complete \
     -H "Content-Type: application/json" \
     -H "X-Webhook-Secret: your-webhook-secret" \
     -d '{"event":"scan_complete"}'

You should see email delivery triggered in Render logs.

Then check /api/picks to see the results:
   https://your-api.onrender.com/api/picks/free


═══════════════════════════════════════════════════════
ENVIRONMENT VARIABLES SUMMARY
═══════════════════════════════════════════════════════

Set all of these in Render dashboard:

FMP_API_KEY        Financial Modeling Prep API key
ANTHROPIC_API_KEY  Anthropic Claude API key
BEEHIIV_API_KEY    Beehiiv API key
BEEHIIV_PUB_ID     Beehiiv publication ID (pub_xxxxxxxx)
WEBHOOK_SECRET     Any long random string you make up
API_URL            Your Render URL (https://xxx.onrender.com)
OUTPUT_DIR         ./output
PORT               8080


═══════════════════════════════════════════════════════
TROUBLESHOOTING
═══════════════════════════════════════════════════════

Website shows old data:
→ Check API_URL in index.html matches your Render URL exactly
→ Open browser console — look for fetch errors
→ Visit /api/status to confirm API is running

Email not sending:
→ Visit /api/status — check beehiiv_configured: true
→ Check BEEHIIV_API_KEY and BEEHIIV_PUB_ID are set correctly
→ Check Render logs for [Email] error messages

Scanner not running:
→ Check Render Cron Job logs
→ Run manually: python scanner/scanner.py
→ Confirm FMP_API_KEY is set and valid

No picks showing after scan:
→ Check output/ folder has scan_US_YYYY-MM-DD.json
→ Stocks may all be filtered out — lower max_debt_equity to 1.0 temporarily
→ Check FMP free tier hasn't hit 250 call/day limit

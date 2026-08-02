"""
Add this to scanner.py — call notify_scan_complete() at the end of ValueScanner.run()
"""

import urllib.request
import json
import os

def notify_scan_complete():
    """
    Calls the API server webhook after a scan finishes.
    This triggers the automated email send to all subscribers.
    """
    api_url = os.getenv("API_URL", "http://localhost:8080")
    secret  = os.getenv("WEBHOOK_SECRET", "change-this-secret")

    payload = json.dumps({"event": "scan_complete"}).encode()
    req = urllib.request.Request(
        f"{api_url}/api/webhook/scan-complete",
        data=payload,
        headers={
            "Content-Type":    "application/json",
            "X-Webhook-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode())
            print(f"\n✓ Email triggered — {result.get('picks')} picks queued for delivery")
    except Exception as e:
        print(f"\n[Warning] Could not notify API server: {e}")
        print("  Email will not be sent automatically this run.")
        print(f"  Make sure API server is running at {api_url}")

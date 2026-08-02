"""
ValueScan Scheduler
Runs the scanner after each market close (4:30 PM ET weekdays).
Deploy on any server with: python scheduler.py
For global: add EU (5 PM UTC) and JP (6 AM UTC) scan windows.
"""

import time
import subprocess
from datetime import datetime
import zoneinfo

ET = zoneinfo.ZoneInfo("America/New_York")

def is_market_day() -> bool:
    """Basic weekday check. Extend with holidays list for production."""
    return datetime.now(ET).weekday() < 5  # Mon–Fri

def minutes_until_scan() -> int:
    """Returns minutes until next scan (4:30 PM ET on trading days)."""
    now = datetime.now(ET)
    target = now.replace(hour=16, minute=30, second=0, microsecond=0)
    if now >= target:
        # Already past today's scan — schedule for tomorrow
        from datetime import timedelta
        target = target + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
    delta = (target - now).total_seconds() / 60
    return int(delta)

def run_scan():
    print(f"\n[{datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}] Starting daily scan...")
    subprocess.run(["python", "scanner.py"], check=True)
    print(f"[{datetime.now(ET).strftime('%H:%M ET')}] Scan complete.")

if __name__ == "__main__":
    print("ValueScan Scheduler started.")
    print("Runs every trading day at 4:30 PM ET.\n")

    while True:
        if is_market_day():
            wait = minutes_until_scan()
            print(f"Next scan in {wait // 60}h {wait % 60}m")
            time.sleep(wait * 60)
            run_scan()
        else:
            print("Weekend — no scan today.")
            time.sleep(60 * 60 * 12)  # Check again in 12 hours

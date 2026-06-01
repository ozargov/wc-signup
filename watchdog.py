#!/usr/bin/env python3
"""
WC Signup Watchdog
Runs on a cron schedule (every 5 minutes via GitHub Actions).
Reads schedule.json, finds bookings whose signup window opens within the next
LOOKAHEAD_MINUTES minutes, and runs book_class.py for each one.
After processing, removes completed entries and commits the updated schedule.json.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_PATH   = os.path.join(SCRIPT_DIR, "schedule.json")
BOOK_SCRIPT     = os.path.join(SCRIPT_DIR, "book_class.py")
LOOKAHEAD_MIN   = 10   # fire if window opens within this many minutes


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def hours_before_for_class(target_dt):
    """72h for Sunday (js_dow=0) and Monday (js_dow=1) classes, 48h otherwise."""
    js_dow = (target_dt.weekday() + 1) % 7
    return 72 if js_dow in (0, 1) else 48


def load_schedule():
    if not os.path.exists(SCHEDULE_PATH):
        return {"bookings": []}
    with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(data):
    with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def commit_schedule(removed):
    """Commit updated schedule.json back to the repo via git."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log("No GITHUB_TOKEN — skipping commit.")
        return
    try:
        subprocess.run(["git", "config", "user.email", "wc-signup-bot@users.noreply.github.com"], check=True)
        subprocess.run(["git", "config", "user.name",  "WC Signup Bot"], check=True)
        subprocess.run(["git", "add", SCHEDULE_PATH], check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            log("No changes to commit.")
            return
        labels = [f"{b['class_date']} {b['class_time']}" for b in removed]
        msg = "Remove processed bookings: " + ", ".join(labels)
        subprocess.run(["git", "commit", "-m", msg], check=True)
        subprocess.run(["git", "push"], check=True)
        log("schedule.json committed and pushed.")
    except subprocess.CalledProcessError as e:
        log(f"Warning: git commit failed: {e}")


def main():
    data     = load_schedule()
    bookings = data.get("bookings", [])

    if not bookings:
        log("No pending bookings in schedule.json.")
        return

    log(f"Checking {len(bookings)} pending booking(s)...")

    now        = datetime.now()
    due        = []
    remaining  = []

    for b in bookings:
        try:
            target_dt    = datetime.strptime(f"{b['class_date']} {b['class_time']}", "%Y-%m-%d %H:%M")
            hours_before = hours_before_for_class(target_dt)
            window_opens = target_dt - timedelta(hours=hours_before)
            delta_min    = (window_opens - now).total_seconds() / 60

            if delta_min <= LOOKAHEAD_MIN and delta_min > -60:
                log(f"  DUE:     {b['class_date']} {b['class_time']} [{b.get('category_filter','')}]  "
                    f"window opens {window_opens.strftime('%Y-%m-%d %H:%M')} (delta {delta_min:.1f}min)")
                due.append(b)
            elif window_opens < now - timedelta(hours=1):
                log(f"  EXPIRED: {b['class_date']} {b['class_time']} (window was {window_opens.strftime('%Y-%m-%d %H:%M')})")
                # drop from schedule silently
            else:
                log(f"  PENDING: {b['class_date']} {b['class_time']} [{b.get('category_filter','')}]  "
                    f"window opens {window_opens.strftime('%Y-%m-%d %H:%M')} (in {delta_min:.0f}min)")
                remaining.append(b)
        except Exception as e:
            log(f"  ERROR parsing booking {b}: {e}")
            remaining.append(b)

    if not due:
        log(f"Nothing due in the next {LOOKAHEAD_MIN} minutes.")
        return

    processed = []
    for b in due:
        log(f"\n--- Booking: {b['class_date']} {b['class_time']} [{b.get('category_filter','')}] ---")
        cmd = [
            sys.executable, BOOK_SCRIPT,
            "--class-date",   b["class_date"],
            "--class-time",   b["class_time"],
            "--wait-for-window",
        ]
        if b.get("category_filter"):
            cmd += ["--category-filter", b["category_filter"]]

        result = subprocess.run(cmd, env=os.environ.copy())
        status = "SUCCESS" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        log(f"--- {status} ---")
        processed.append(b)   # always remove — don't retry infinitely on full class

    # Persist the updated schedule
    data["bookings"] = remaining
    save_schedule(data)
    commit_schedule(processed)


if __name__ == "__main__":
    main()

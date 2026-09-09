#!/usr/bin/env python3
"""
WC Signup - the booking queue.

bookings.json holds the classes you want. Two GitHub Actions workflows drive it:

  add-booking.yml  "Add booking" button    ->  bookings.py add / remove
  book-due.yml     every 30 minutes        ->  bookings.py run-due

run-due picks up any queued class whose signup window opens within the lookahead
and hands it to book_class.py --wait-for-window, which sleeps to the exact second
before firing. That is what makes the booking punctual even though GitHub's timer
is not: GitHub only has to start the job roughly on time.

Usage:
  python bookings.py add    --date 2026-09-14 --time 08:00 --category "Hall A"
  python bookings.py remove --date 2026-09-14 --time 08:00
  python bookings.py list
  python bookings.py run-due --lookahead-minutes 45
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from book_class import hours_before_for, window_opens_for

HERE        = Path(__file__).resolve().parent
QUEUE_PATH  = HERE / "bookings.json"
BOOK_SCRIPT = HERE / "book_class.py"

# How long after a window opens we still consider a booking worth attempting.
# Past that the class is long gone and the entry is dropped -- though with the
# waiting list in play, a late attempt is still worth making.
GRACE_MINUTES = 60

# book_class.py exits 6 when it could not book but did join the waiting list.
WAITLISTED_EXIT = 6


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Queue file
# ---------------------------------------------------------------------------
def load():
    if not QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log(f"bookings.json is not valid JSON ({e}). Treating the queue as empty.")
        return []
    if isinstance(data, dict):
        return data.get("bookings") or []
    return list(data) if isinstance(data, list) else []


def save(bookings):
    bookings.sort(key=lambda b: (b.get("date", ""), b.get("time", "")))
    QUEUE_PATH.write_text(
        json.dumps({"bookings": bookings}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_target(date_s, time_s):
    """Validate and combine the date and time, with a friendly error."""
    try:
        return datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise SystemExit(
            f"Could not read '{date_s} {time_s}'. Expected a date like 2026-09-14 "
            f"and a 24-hour time like 08:00 or 18:15."
        )


def describe(b):
    cat = b.get("category") or ""
    return f"{b['date']} {b['time']}" + (f" ({cat})" if cat else "")


# ---------------------------------------------------------------------------
# Telegram notification (a one-off send, so it needs no always-on server)
# ---------------------------------------------------------------------------
def notify(text):
    token = os.environ.get("BOT_TOKEN", "")
    chat  = os.environ.get("OWNER_CHAT_ID", "")
    if not token or not chat or chat == "0":
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req  = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as e:
        log(f"(could not send the Telegram message: {e})")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_add(args):
    target = parse_target(args.date, args.time)
    window = window_opens_for(target)
    cat    = (args.category or "").strip()

    if target < datetime.now():
        raise SystemExit(f"{describe({'date': args.date, 'time': args.time, 'category': cat})} "
                         f"is in the past. Nothing to book.")

    bookings = load()
    for b in bookings:
        if b["date"] == args.date and b["time"] == args.time:
            log(f"Already queued: {describe(b)} - leaving the queue unchanged.")
            return
    entry = {
        "date":     args.date,
        "time":     args.time,
        "category": cat,
        "added":    datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    bookings.append(entry)
    save(bookings)  # note: sorts in place, so describe `entry`, not bookings[-1]

    log(f"Queued {describe(entry)}")
    log(f"  signup window opens {window.strftime('%A %d %b at %H:%M')} "
        f"({hours_before_for(target)}h before the class)")
    notify(
        f"Queued: {describe(entry)}\n"
        f"Books automatically on {window.strftime('%a %d %b at %H:%M')}."
    )


def cmd_remove(args):
    parse_target(args.date, args.time)  # validate the format
    bookings = load()
    keep = [b for b in bookings if not (b["date"] == args.date and b["time"] == args.time)]
    if len(keep) == len(bookings):
        log(f"Nothing queued for {args.date} {args.time}.")
        return
    save(keep)
    log(f"Removed {args.date} {args.time} from the queue.")
    notify(f"Removed: {args.date} {args.time}")


def cmd_list(args):
    bookings = load()
    if not bookings:
        log("The queue is empty.")
        return
    now = datetime.now()
    log(f"{len(bookings)} class(es) queued:")
    for b in bookings:
        target = parse_target(b["date"], b["time"])
        window = window_opens_for(target)
        mins   = (window - now).total_seconds() / 60
        if mins > 60:
            when = f"books in {mins/60:.1f}h ({window.strftime('%a %d %b %H:%M')})"
        elif mins > 0:
            when = f"books in {mins:.0f} min"
        else:
            when = f"window opened {abs(mins):.0f} min ago"
        log(f"  - {describe(b):<40} {when}")


def cmd_run_due(args):
    bookings = load()
    if not bookings:
        log("The queue is empty - nothing to do.")
        return 0

    now  = datetime.now()
    due, keep, expired = [], [], []

    for b in bookings:
        try:
            target = datetime.strptime(f"{b['date']} {b['time']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            log(f"  skipping malformed entry: {b!r}")
            keep.append(b)
            continue
        window = window_opens_for(target)
        mins   = (window - now).total_seconds() / 60
        if mins < -GRACE_MINUTES:
            log(f"  EXPIRED  {describe(b)} - window opened {abs(mins)/60:.1f}h ago")
            expired.append(b)
        elif mins <= args.lookahead_minutes:
            log(f"  DUE      {describe(b)} - window at {window.strftime('%H:%M')} "
                f"(in {mins:.1f} min)")
            due.append((b, window))
        else:
            log(f"  waiting  {describe(b)} - window in {mins/60:.1f}h")
            keep.append(b)

    if not due:
        log(f"Nothing due within the next {args.lookahead_minutes} minutes.")
        if expired:
            save(keep)
        return 0

    # Book the earliest window first, so a sleeping job never delays a nearer one.
    due.sort(key=lambda pair: pair[1])

    failures = []
    for b, window in due:
        log(f"\n--- {describe(b)} - waiting for {window.strftime('%H:%M:%S')} ---")
        cmd = [
            sys.executable, str(BOOK_SCRIPT),
            "--class-date", b["date"],
            "--class-time", b["time"],
            "--wait-for-window",
        ]
        if b.get("category"):
            cmd += ["--category-filter", b["category"]]
        if args.dry_run:
            cmd.append("--dry-run")

        result = subprocess.run(cmd, env=os.environ.copy())
        if result.returncode == 0:
            log(f"--- BOOKED {describe(b)} ---")
            notify(f"Booked: {describe(b)}")
        elif result.returncode == WAITLISTED_EXIT:
            # The class filled before we got in, but we are on the waiting list,
            # which is unlimited and auto-promotes when someone drops out.
            log(f"--- WAITLISTED {describe(b)} ---")
            notify(
                f"Class was full: {describe(b)}\n"
                f"You are on the waiting list - Arbox moves you in automatically "
                f"if someone cancels."
            )
        else:
            log(f"--- FAILED {describe(b)} (exit {result.returncode}) ---")
            failures.append(b)
            notify(
                f"Booking FAILED: {describe(b)}\n"
                f"Open the Actions tab on GitHub to see why."
            )

    # Attempted entries always leave the queue, successful or not. Retrying a
    # full class forever would just fail on every poll for the next hour.
    save(keep)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Manage the WC Signup booking queue")
    sub = ap.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="queue a class")
    p_add.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_add.add_argument("--time", required=True, help="HH:MM (24h)")
    p_add.add_argument("--category", default="", help="part of the class name, optional")

    p_rm = sub.add_parser("remove", help="remove a queued class")
    p_rm.add_argument("--date", required=True)
    p_rm.add_argument("--time", required=True)
    p_rm.add_argument("--category", default="", help="ignored; matching is on date and time")

    sub.add_parser("list", help="show the queue")

    p_run = sub.add_parser("run-due", help="book anything whose window opens soon")
    p_run.add_argument("--lookahead-minutes", type=int, default=45)
    p_run.add_argument("--dry-run", action="store_true",
                       help="go through the motions without actually booking")

    args = ap.parse_args()
    if args.command == "add":
        return cmd_add(args) or 0
    if args.command == "remove":
        return cmd_remove(args) or 0
    if args.command == "list":
        return cmd_list(args) or 0
    if args.command == "run-due":
        return cmd_run_due(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

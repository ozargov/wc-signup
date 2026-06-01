#!/usr/bin/env python3
"""
WC Signup - CrossFit White City class booking script
Reads credentials from environment variables (GitHub Secrets) or config.json.

Usage:
  python book_class.py --class-date 2026-06-04 --class-time 08:00
  python book_class.py --class-date 2026-06-04 --class-time 08:00 --category-filter "W.O.D Hall A" --wait-for-window
  python book_class.py --class-date 2026-06-04 --class-time 08:00 --dry-run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

BASE_URL = "https://apiappv2.arboxapp.com/api/v2"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "booking.log")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg, color=None):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _headers(token=None, refresh=None, whitelabel="HYPR-training"):
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        "Origin": "https://app.arboxapp.com",
        "Referer": "https://app.arboxapp.com/",
        "whitelabel": whitelabel,
        "referername": "app",
        "version": "11",
    }
    if token:
        h["accesstoken"] = token
    if refresh:
        h["refreshtoken"] = refresh
    return h


def _post(url, headers, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_bytes = b""
        try:
            body_bytes = e.read()
        except Exception:
            pass
        try:
            body_text = body_bytes.decode("utf-8")
        except Exception:
            body_text = repr(body_bytes)
        return e.code, body_text


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------
def login(email, password, whitelabel):
    log("Step 1: login...")
    status, resp = _post(
        f"{BASE_URL}/user/login",
        _headers(whitelabel=whitelabel),
        {"email": email, "password": password},
    )
    if status != 200:
        log(f"Login failed (HTTP {status}): {str(resp)[:300]}")
        sys.exit(2)
    data = (resp.get("data") or resp) if isinstance(resp, dict) else {}
    token = data.get("token")
    refresh = data.get("refreshToken") or data.get("refresh_token", "")
    if not token:
        log("Login returned no token.")
        sys.exit(2)
    log(f"  logged in (token len {len(token)})")
    return token, refresh


def fetch_schedule(class_date, locations_box_id, boxes_id, token, refresh, whitelabel):
    log(f"Step 2: fetching schedule for {class_date}...")
    iso = f"{class_date}T00:00:00.000Z"
    status, resp = _post(
        f"{BASE_URL}/schedule/betweenDates",
        _headers(token, refresh, whitelabel),
        {
            "from": iso,
            "to": iso,
            "locations_box_id": locations_box_id,
            "boxes_id": boxes_id,
        },
    )
    if status != 200:
        log(f"Schedule fetch HTTP {status}: {str(resp)[:300]}")
        sys.exit(3)
    classes = (resp.get("data") or resp) if isinstance(resp, dict) else resp
    if not isinstance(classes, list):
        classes = []
    log(f"  got {len(classes)} classes for {class_date}")
    return classes


def find_class(classes, class_time, category_filter):
    found = []
    for cls in classes:
        tval = str(cls.get("time", ""))[:5]
        if tval != class_time:
            continue
        if category_filter:
            name = str((cls.get("box_categories") or {}).get("name", ""))
            if category_filter.lower() not in name.lower():
                continue
        found.append(cls)
    return found


def book_class(schedule_id, membership_user_id, token, refresh, whitelabel):
    status, resp = _post(
        f"{BASE_URL}/scheduleUser/insert",
        _headers(token, refresh, whitelabel),
        {
            "schedule_id": int(schedule_id),
            "membership_user_id": int(membership_user_id),
            "extras": None,
        },
    )
    return status, resp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Book a CrossFit White City class")
    parser.add_argument("--class-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--class-time", required=True, help="HH:MM (24h)")
    parser.add_argument("--category-filter", default="", help="Substring match on category name")
    parser.add_argument("--wait-for-window", action="store_true", help="Sleep until signup window opens")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually book, just print what would happen")
    args = parser.parse_args()

    # --- Credentials: env vars first, then config.json ---
    email    = os.environ.get("ARBOX_EMAIL")
    password = os.environ.get("ARBOX_PASSWORD")
    whitelabel       = os.environ.get("ARBOX_WHITELABEL", "HYPR-training")
    boxes_id         = int(os.environ.get("ARBOX_BOXES_ID", "59"))
    loc_box_id       = int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "48"))
    membership_uid   = int(os.environ.get("ARBOX_MEMBERSHIP_USER_ID", "13705578"))

    if not email or not password:
        cfg_path = os.path.join(SCRIPT_DIR, "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            email          = cfg.get("email", email)
            password       = cfg.get("password", password)
            whitelabel     = cfg.get("whitelabel", whitelabel)
            boxes_id       = int(cfg.get("boxes_id", boxes_id))
            loc_box_id     = int(cfg.get("locations_box_id", loc_box_id))
            membership_uid = int(cfg.get("membership_user_id", membership_uid))

    if not email or not password:
        log("No credentials. Set ARBOX_EMAIL + ARBOX_PASSWORD env vars, or provide config.json.")
        sys.exit(1)

    # --- Parse target time ---
    try:
        target_dt = datetime.strptime(f"{args.class_date} {args.class_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        log("Invalid --class-date / --class-time. Expected YYYY-MM-DD and HH:MM.")
        sys.exit(1)

    # Window opens 48h before class (72h for Sunday and Monday classes)
    js_dow = (target_dt.weekday() + 1) % 7  # Mon=1 ... Sun=0 to match JS convention
    hours_before = 72 if js_dow in (0, 1) else 48
    window_opens = target_dt - timedelta(hours=hours_before)

    log("=" * 50)
    log(f"Target class: {target_dt.strftime('%Y-%m-%d %H:%M %A')}")
    log(f"Window opens: {window_opens.strftime('%Y-%m-%d %H:%M:%S')} ({hours_before}h before class)")
    if args.category_filter:
        log(f"Category filter: '{args.category_filter}'")
    log(f"Now:          {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"DryRun:       {args.dry_run}")

    # --- Login + fetch schedule ---
    token, refresh = login(email, password, whitelabel)
    classes = fetch_schedule(args.class_date, loc_box_id, boxes_id, token, refresh, whitelabel)

    # --- Find target class ---
    candidates = find_class(classes, args.class_time, args.category_filter)
    if not candidates:
        log(f"No class found at {args.class_time} on {args.class_date} (filter='{args.category_filter}').")
        log("Available classes:")
        for cls in classes:
            t     = str(cls.get("time", ""))[:5]
            cat   = str((cls.get("box_categories") or {}).get("name", ""))
            coach = str((cls.get("coach") or {}).get("full_name", ""))
            log(f"  {t}  {cat}  ({coach})")
        sys.exit(4)

    if len(candidates) > 1:
        log("Multiple classes match — using the first. Refine with --category-filter.")
        for cls in candidates:
            log(f"  candidate id={cls['id']} cat='{(cls.get('box_categories') or {}).get('name','')}' coach='{(cls.get('coach') or {}).get('full_name','')}'")

    target      = candidates[0]
    schedule_id = target["id"]
    cat_name    = str((target.get("box_categories") or {}).get("name", ""))
    coach_name  = str((target.get("coach") or {}).get("full_name", ""))
    free        = target.get("free", "?")
    log(f"Target found: id={schedule_id}  {target.get('time','')}  {cat_name}  coach={coach_name}  free={free}")

    # --- Wait for signup window ---
    if args.wait_for_window:
        now = datetime.now()
        delta_s = (window_opens - now).total_seconds()
        if delta_s <= 0:
            log("Window already open (or past) — firing immediately.")
        else:
            log(f"Waiting {delta_s:.1f}s until window opens...")
            if delta_s > 1.5:
                time.sleep(delta_s - 1.0)
            # Tight spin for the last second
            while datetime.now() < window_opens:
                time.sleep(0.01)
            log(f"Window OPEN at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

    # --- Dry run ---
    if args.dry_run:
        log(f"DRY RUN — would POST /scheduleUser/insert for schedule_id={schedule_id}")
        log("Done (dry run).")
        sys.exit(0)

    # --- Book with retry ---
    log("Step 5: POST /scheduleUser/insert ...")
    max_attempts = 8
    delay_s = 0.2
    success = False

    for i in range(1, max_attempts + 1):
        status, resp = book_class(schedule_id, membership_uid, token, refresh, whitelabel)
        if status == 200:
            log(f"Attempt {i} SUCCESS at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            success = True
            break
        else:
            log(f"Attempt {i} HTTP {status} : {str(resp)[:200]}")
            # Don't retry on permanent 4xx (except rate-limit / timeout codes)
            if isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429):
                log("Permanent failure — not retrying.")
                break
            time.sleep(delay_s)

    if not success:
        log(f"BOOKING FAILED after {max_attempts} attempts.")
        sys.exit(5)

    log(f"BOOKING CONFIRMED for {target_dt.strftime('%Y-%m-%d %H:%M')} — {cat_name}")
    sys.exit(0)


if __name__ == "__main__":
    main()

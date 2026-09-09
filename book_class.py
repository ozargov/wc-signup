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
# Environment helpers
# ---------------------------------------------------------------------------
def _env(name, default=""):
    """Treat an unset AND an empty variable as "not provided". GitHub Actions
    substitutes an empty string for a secret that does not exist, which would
    otherwise turn int(os.environ[...]) into a crash."""
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def _env_int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        log(f"  {name} is not a whole number - using {default}")
        return int(default)


# ---------------------------------------------------------------------------
# Signup window rule (the single definition -- bookings.py imports these)
# ---------------------------------------------------------------------------
def hours_before_for(target_dt):
    """Classes open 48h ahead, except Sunday and Monday classes, which open 72h
    ahead (i.e. Thursday / Friday at the same time of day)."""
    return 72 if target_dt.weekday() in (0, 6) else 48


def window_opens_for(target_dt):
    """The moment a class becomes bookable."""
    return target_dt - timedelta(hours=hours_before_for(target_dt))


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


def _decompress(data):
    """Decompress gzip/deflate response bytes if needed."""
    if data[:2] == b'\x1f\x8b':
        import gzip
        return gzip.decompress(data)
    if data[:2] in (b'x\x9c', b'x\x01', b'x\xda'):
        import zlib
        return zlib.decompress(data)
    return data

LAST_POST_SECONDS = 0.30   # updated by _post; used to time the opening request


def _post(url, headers, body):
    global LAST_POST_SECONDS
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    _started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = _decompress(resp.read())
            LAST_POST_SECONDS = time.monotonic() - _started
            return resp.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        LAST_POST_SECONDS = time.monotonic() - _started
        body_bytes = b""
        try:
            body_bytes = _decompress(e.read())
        except Exception:
            pass
        try:
            body_text = body_bytes.decode("utf-8")
        except Exception:
            body_text = repr(body_bytes)
        return e.code, body_text
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # A dropped connection or timeout is exactly what happens when everyone
        # hits the API in the same second. Report it as a transient 599 so the
        # caller's retry loop kicks in instead of the exception killing the run.
        return 599, f"network error: {e}"


# ---------------------------------------------------------------------------
# Race tuning
#
# Popular classes here fill in about five seconds, so the first request has to
# land in the opening moment. Two measured facts from booking.log shape this:
#   * HTTP 516 "Schedule Is Full" is how a full class is reported.
#   * The API rate-limits after roughly 4 requests in ~1.1s (HTTP 429), so
#     hammering harder than that is counter-productive.
# ---------------------------------------------------------------------------
BURST_SECONDS      = 15.0   # keep trying to book for this long after the window
ATTEMPT_GAP        = 0.32   # ~3 requests/sec: under the observed 429 threshold
RATE_LIMIT_BACKOFF = 1.20   # pause this long after a 429
MIN_LEAD           = 0.05   # never fire more than this early...
MAX_LEAD           = 0.50   # ...and never more than this
FULL_STATUS        = 516    # "Schedule Is Full"

# The waiting list is unlimited, so if the class fills we join it immediately --
# waitlists are ordered by join time, so being fast still matters.
#
# NOTE: this path is not published by Arbox and is not yet confirmed from a real
# request. Each candidate is tried in order until one is accepted, and the
# winner is logged so it can be pinned down. Every candidate is waitlist-named;
# none can cancel or delete anything, and a wrong path just returns 404.
WAITLIST_CANDIDATES = [
    ("/scheduleUser/insertToWaitingList", "schedule"),
    ("/scheduleUser/insertWaitingList",   "schedule"),
    ("/scheduleUser/waitingList",         "schedule"),
    ("/waitingList/insert",               "schedule"),
    ("/scheduleUser/standby",             "schedule"),
    ("/scheduleUser/insert",              "flagged"),   # same call, waiting_list flag
]


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------
def login(email, password, whitelabel):
    log("Step 1: login...")
    # Retry transient failures. This runs well before the signup window, so a
    # few seconds spent here costs nothing -- but giving up means no booking.
    for attempt in range(1, 4):
        status, resp = _post(
            f"{BASE_URL}/user/login",
            _headers(whitelabel=whitelabel),
            {"email": email, "password": password},
        )
        if status == 200:
            break
        permanent = isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429)
        log(f"  login attempt {attempt} failed (HTTP {status}): {str(resp)[:200]}")
        if permanent:
            log("  credentials rejected -- not retrying.")
            break
        if attempt < 3:
            time.sleep(3)
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
    for attempt in range(1, 4):
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
        if status == 200:
            break
        log(f"  schedule attempt {attempt} failed (HTTP {status}): {str(resp)[:200]}")
        if attempt < 3:
            time.sleep(3)
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


def is_full(status, resp):
    """True when the API is telling us the class has no free spots."""
    if status == FULL_STATUS:
        return True
    return "schedule is full" in str(resp).lower()


def join_waiting_list(schedule_id, membership_user_id, token, refresh, whitelabel):
    """Try the waiting-list endpoints in turn; return (path, resp) on success."""
    hdrs = _headers(token, refresh, whitelabel)
    base = {
        "schedule_id": int(schedule_id),
        "membership_user_id": int(membership_user_id),
        "extras": None,
    }
    for path, kind in WAITLIST_CANDIDATES:
        body = dict(base)
        if kind == "flagged":
            body["waiting_list"] = True
        status, resp = _post(f"{BASE_URL}{path}", hdrs, body)
        if status == 200:
            log(f"  waiting list JOINED via {path}")
            return path, resp
        if status == 429:
            time.sleep(RATE_LIMIT_BACKOFF)
            status, resp = _post(f"{BASE_URL}{path}", hdrs, body)
            if status == 200:
                log(f"  waiting list JOINED via {path} (after backoff)")
                return path, resp
        log(f"  waiting list: {path} -> HTTP {status} {str(resp)[:120]}")
    return None, None


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
    parser.add_argument("--burst-seconds", type=float, default=BURST_SECONDS,
                        help="How long to keep retrying the booking (default %(default)s)")
    parser.add_argument("--no-waitlist", action="store_true",
                        help="Do not join the waiting list if the class is full")
    args = parser.parse_args()

    # --- Credentials: env vars first, then config.json ---
    email    = _env("ARBOX_EMAIL")
    password = _env("ARBOX_PASSWORD")
    whitelabel     = _env("ARBOX_WHITELABEL", "HYPR-training")
    boxes_id       = _env_int("ARBOX_BOXES_ID", 59)
    loc_box_id     = _env_int("ARBOX_LOCATIONS_BOX_ID", 48)
    membership_uid = _env_int("ARBOX_MEMBERSHIP_USER_ID", 13705578)

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

    hours_before = hours_before_for(target_dt)
    window_opens = window_opens_for(target_dt)

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

    # --- Wait for the signup window ---
    # Send slightly early so the request ARRIVES as the window opens, rather
    # than leaving as it opens. The lead is half the round-trip just measured
    # against this very API, clamped to something sane.
    lead = min(MAX_LEAD, max(MIN_LEAD, LAST_POST_SECONDS / 2.0))
    fire_at = window_opens - timedelta(seconds=lead)

    if args.wait_for_window:
        delta_s = (fire_at - datetime.now()).total_seconds()
        log(f"Round-trip to the API measured at {LAST_POST_SECONDS*1000:.0f}ms "
            f"-> firing {lead*1000:.0f}ms early.")
        if delta_s <= 0:
            log("Window already open (or past) - firing immediately.")
        else:
            log(f"Waiting {delta_s:.1f}s (until {fire_at.strftime('%H:%M:%S.%f')[:-3]})...")
            if delta_s > 1.5:
                time.sleep(delta_s - 1.0)
            # Tight spin for the last stretch, finer than before: a class here
            # fills in ~5s, so 10ms of slop is worth removing.
            while datetime.now() < fire_at:
                time.sleep(0.002)
        log(f"FIRING at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
            f"(window opens {window_opens.strftime('%H:%M:%S')})")

    # --- Dry run ---
    if args.dry_run:
        log(f"DRY RUN - would POST /scheduleUser/insert for schedule_id={schedule_id}")
        log(f"DRY RUN - would fall back to the waiting list on HTTP {FULL_STATUS}")
        log("Done (dry run).")
        sys.exit(0)

    # --- Book: burst until the deadline, pacing under the rate limit ---
    log(f"Step 5: POST /scheduleUser/insert (up to {args.burst_seconds:.0f}s, "
        f"~{1/ATTEMPT_GAP:.1f} req/s) ...")
    deadline  = time.monotonic() + args.burst_seconds
    attempt   = 0
    booked    = False
    saw_full  = False
    permanent = False

    while time.monotonic() < deadline:
        attempt += 1
        status, resp = book_class(schedule_id, membership_uid, token, refresh, whitelabel)

        if status == 200:
            log(f"Attempt {attempt} SUCCESS at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            booked = True
            break

        if is_full(status, resp):
            # No point hammering a full class - and the waiting list is ordered
            # by join time, so every moment spent retrying costs us a place.
            log(f"Attempt {attempt}: class is FULL (HTTP {status}) - going straight "
                f"to the waiting list.")
            saw_full = True
            break

        if status == 429:
            log(f"Attempt {attempt}: rate limited - backing off {RATE_LIMIT_BACKOFF}s")
            time.sleep(RATE_LIMIT_BACKOFF)
            continue

        if isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429):
            log(f"Attempt {attempt} HTTP {status} : {str(resp)[:200]}")
            log("Permanent failure - not retrying.")
            permanent = True
            break

        log(f"Attempt {attempt} HTTP {status} : {str(resp)[:200]}")
        time.sleep(ATTEMPT_GAP)

    if booked:
        log(f"BOOKING CONFIRMED for {target_dt.strftime('%Y-%m-%d %H:%M')} - {cat_name}")
        sys.exit(0)

    if permanent and not saw_full:
        log("BOOKING FAILED - the API rejected the request outright.")
        sys.exit(5)

    if args.no_waitlist:
        log(f"Not booked after {attempt} attempt(s), and --no-waitlist was given.")
        sys.exit(5)

    # --- Waiting list fallback (unlimited, so this is always worth doing) ---
    log("Step 6: joining the waiting list ...")
    path, _resp = join_waiting_list(
        schedule_id, membership_uid, token, refresh, whitelabel
    )
    if path:
        log(f"WAITLISTED for {target_dt.strftime('%Y-%m-%d %H:%M')} - {cat_name}")
        sys.exit(6)

    log("Could not book the class and could not join the waiting list.")
    log("None of the candidate waiting-list endpoints was accepted - the real "
        "one needs to be captured from the app (see SETUP.md).")
    sys.exit(5)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
WC Signup Telegram Bot
Books CrossFit White City classes automatically.

Environment variables (set in Railway dashboard):
  BOT_TOKEN                Telegram bot token from @BotFather
  OWNER_CHAT_ID            Your Telegram user ID  — get it by sending /myid to the bot
  ARBOX_EMAIL              Your Arbox login email
  ARBOX_PASSWORD           Your Arbox password
  ARBOX_WHITELABEL         (optional, default: HYPR-training)
  ARBOX_BOXES_ID           (optional, default: 59)
  ARBOX_LOCATIONS_BOX_ID   (optional, default: 48)
  ARBOX_MEMBERSHIP_USER_ID (optional, default: 13705578)
  TZ                       (optional, default: Asia/Jerusalem) — leave as Asia/Jerusalem
  DATA_DIR                 (optional) path for schedule.json — set to /data if using a Railway Volume
"""

import asyncio
import gzip
import json
import logging
import os
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID  = int(os.environ.get("OWNER_CHAT_ID", "0"))  # 0 = not set yet

ARBOX_EMAIL       = os.environ.get("ARBOX_EMAIL", "")
ARBOX_PASSWORD    = os.environ.get("ARBOX_PASSWORD", "")
ARBOX_WHITELABEL  = os.environ.get("ARBOX_WHITELABEL", "HYPR-training")
ARBOX_BOXES_ID    = int(os.environ.get("ARBOX_BOXES_ID", "59"))
ARBOX_LOC_BOX_ID  = int(os.environ.get("ARBOX_LOCATIONS_BOX_ID", "48"))
ARBOX_MEMBER_UID  = int(os.environ.get("ARBOX_MEMBERSHIP_USER_ID", "13705578"))

# Arbox reports every class time in Israel local time, and this bot compares
# those times against "now". A cloud container runs UTC, so read the clock in
# Israel explicitly -- otherwise every signup window is computed 2-3h late.
TZ = ZoneInfo(os.environ.get("TZ") or "Asia/Jerusalem")


def now() -> datetime:
    """Current Israel wall-clock time, naive, to compare with class times."""
    return datetime.now(TZ).replace(tzinfo=None)


BASE_URL   = "https://apiappv2.arboxapp.com/api/v2"
DATA_DIR   = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
SCHED_FILE = DATA_DIR / "schedule.json"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# timezone=TZ makes the naive run_date values below mean Israel time, not UTC.
scheduler = AsyncIOScheduler(timezone=TZ)

# Conversation states
DATE, TIME, CATEGORY = range(3)


# ── Arbox API helpers ─────────────────────────────────────────────────────────
def _decompress(b: bytes) -> bytes:
    if b[:2] == b"\x1f\x8b":
        return gzip.decompress(b)
    if b[:2] in (b"x\x9c", b"x\x01", b"\x78\xda"):
        return zlib.decompress(b)
    return b


def _headers(token: str = None, refresh: str = None) -> dict:
    h = {
        "Content-Type":   "application/json",
        "Accept":         "application/json, text/plain, */*",
        "Accept-Encoding":"gzip, deflate, br",
        "User-Agent":     "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Origin":         "https://app.arboxapp.com",
        "Referer":        "https://app.arboxapp.com/",
        "whitelabel":     ARBOX_WHITELABEL,
        "referername":    "app",
        "version":        "11",
    }
    if token:   h["accesstoken"]  = token
    if refresh: h["refreshtoken"] = refresh
    return h


def _post(url: str, hdrs: dict, body: dict):
    req = urllib.request.Request(
        url, json.dumps(body).encode(), hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(_decompress(r.read()).decode())
    except urllib.error.HTTPError as e:
        try:
            txt = _decompress(e.read()).decode()
        except Exception:
            txt = str(e)
        return e.code, txt


def arbox_login() -> tuple[str, str]:
    s, r = _post(
        f"{BASE_URL}/user/login",
        _headers(),
        {"email": ARBOX_EMAIL, "password": ARBOX_PASSWORD},
    )
    if s != 200:
        raise RuntimeError(f"Login failed ({s}): {str(r)[:200]}")
    d = r.get("data", r) if isinstance(r, dict) else {}
    tok = d.get("token")
    if not tok:
        raise RuntimeError("No token returned from login")
    return tok, d.get("refreshToken", "")


def arbox_fetch_schedule(date_str: str, tok: str, ref: str) -> list:
    iso = f"{date_str}T00:00:00.000Z"
    s, r = _post(
        f"{BASE_URL}/schedule/betweenDates",
        _headers(tok, ref),
        {"from": iso, "to": iso,
         "locations_box_id": ARBOX_LOC_BOX_ID, "boxes_id": ARBOX_BOXES_ID},
    )
    if s != 200:
        raise RuntimeError(f"Schedule fetch failed ({s}): {str(r)[:200]}")
    data = r.get("data", r) if isinstance(r, dict) else r
    return data if isinstance(data, list) else []


def arbox_book(schedule_id: int, tok: str, ref: str):
    return _post(
        f"{BASE_URL}/scheduleUser/insert",
        _headers(tok, ref),
        {"schedule_id": schedule_id, "membership_user_id": ARBOX_MEMBER_UID, "extras": None},
    )


# ── Booking window logic ──────────────────────────────────────────────────────
def window_opens(class_dt: datetime) -> datetime:
    """Returns the datetime when the signup window opens (48h or 72h before)."""
    js_dow = (class_dt.weekday() + 1) % 7  # Mon=1, Sun=0
    hours  = 72 if js_dow in (0, 1) else 48
    return class_dt - timedelta(hours=hours)


# ── Schedule persistence ──────────────────────────────────────────────────────
def load_schedule() -> list:
    try:
        return json.loads(SCHED_FILE.read_text())
    except Exception:
        return []


def save_schedule(bookings: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCHED_FILE.write_text(json.dumps(bookings, indent=2))


# ── Core booking coroutine ────────────────────────────────────────────────────
async def run_booking(
    app, chat_id: int, class_date: str, class_time: str, cat: str
) -> None:
    """Scheduled job: sleeps to exact window-open moment, then books."""
    target_dt = datetime.strptime(f"{class_date} {class_time}", "%Y-%m-%d %H:%M")
    win = window_opens(target_dt)

    # Sleep to the exact moment (APScheduler fires 5 min early)
    delta = (win - now()).total_seconds()
    if delta > 0:
        log.info(f"Sleeping {delta:.1f}s until window opens…")
        await asyncio.sleep(delta)

    # Tight spin for the last milliseconds
    while now() < win:
        await asyncio.sleep(0.005)

    log.info(f"Window OPEN — booking {class_date} {class_time}")

    try:
        tok, ref  = arbox_login()
        classes   = arbox_fetch_schedule(class_date, tok, ref)

        matches = [
            c for c in classes
            if str(c.get("time", ""))[:5] == class_time
            and (
                not cat
                or cat.lower() in str(
                    (c.get("box_categories") or {}).get("name", "")
                ).lower()
            )
        ]

        if not matches:
            avail = ", ".join(str(c.get("time", ""))[:5] for c in classes)
            await app.bot.send_message(
                chat_id,
                f"❌ No class found at {class_time} on {class_date}.\n"
                f"Available times: {avail or 'none'}",
            )
            return

        target    = matches[0]
        sid       = target["id"]
        cat_name  = str((target.get("box_categories") or {}).get("name", ""))
        free_spots = target.get("free", "?")
        log.info(f"Booking id={sid} '{cat_name}' free={free_spots}")

        booked = False
        last_resp = None
        for attempt in range(1, 9):
            status, resp = arbox_book(sid, tok, ref)
            last_resp = resp
            if status == 200:
                booked = True
                log.info(f"Booked on attempt {attempt}")
                break
            log.warning(f"Attempt {attempt} failed ({status}): {str(resp)[:100]}")
            if isinstance(status, int) and 400 <= status < 500 and status not in (408, 425, 429):
                break  # permanent failure, don't retry
            await asyncio.sleep(0.2)

        if booked:
            await app.bot.send_message(
                chat_id,
                f"✅ *Booked!*\n"
                f"*{cat_name}*\n"
                f"{class_time} · {target_dt.strftime('%A, %d %b %Y')}",
                parse_mode="Markdown",
            )
        else:
            await app.bot.send_message(
                chat_id,
                f"❌ Booking failed after 8 attempts.\n"
                f"{class_time} on {class_date}\n"
                f"Last response: {str(last_resp)[:200]}",
            )

    except Exception as e:
        log.exception("Booking error")
        await app.bot.send_message(chat_id, f"❌ Error during booking:\n{e}")

    finally:
        # Always remove from schedule so it doesn't re-fire on restart
        save_schedule(
            [b for b in load_schedule()
             if not (b["class_date"] == class_date and b["class_time"] == class_time)]
        )


# ── Scheduler helpers ─────────────────────────────────────────────────────────
def add_job(app, chat_id: int, class_date: str, class_time: str, cat: str) -> None:
    target_dt = datetime.strptime(f"{class_date} {class_time}", "%Y-%m-%d %H:%M")
    win     = window_opens(target_dt)
    fire_at = win - timedelta(minutes=5)
    job_id  = f"book-{class_date}-{class_time.replace(':', '')}"

    # If fire_at is already past, run immediately (window may already be open)
    run_at = max(fire_at, now() + timedelta(seconds=2))
    scheduler.add_job(
        run_booking, "date", run_date=run_at,
        args=[app, chat_id, class_date, class_time, cat],
        id=job_id, replace_existing=True,
    )

    # Persist
    bookings = [b for b in load_schedule() if b.get("job_id") != job_id]
    bookings.append({
        "job_id":          job_id,
        "chat_id":         chat_id,
        "class_date":      class_date,
        "class_time":      class_time,
        "category_filter": cat,
        "window_opens":    win.isoformat(),
        "fire_at":         fire_at.isoformat(),
    })
    save_schedule(bookings)
    log.info(f"Scheduled {job_id} → fire at {run_at}")


def restore_jobs(app) -> None:
    """On startup, reload pending bookings from schedule.json."""
    bookings = load_schedule()
    started = now()
    kept = []
    for b in bookings:
        win = datetime.fromisoformat(b["window_opens"])
        if win < started - timedelta(hours=2):
            log.info(f"Dropping expired booking {b['class_date']} {b['class_time']}")
            continue
        kept.append(b)
        fire_at = datetime.fromisoformat(b["fire_at"])
        run_at  = max(fire_at, started + timedelta(seconds=2))
        scheduler.add_job(
            run_booking, "date", run_date=run_at,
            args=[app, b["chat_id"], b["class_date"], b["class_time"], b["category_filter"]],
            id=b["job_id"], replace_existing=True,
        )
        log.info(f"Restored {b['job_id']} → fire at {run_at}")
    save_schedule(kept)
    log.info(f"Restored {len(kept)} pending booking(s)")


# ── Auth check ────────────────────────────────────────────────────────────────
def is_owner(update: Update) -> bool:
    if OWNER_ID == 0:
        return False  # not configured yet
    return update.effective_user.id == OWNER_ID


# ── Handlers ──────────────────────────────────────────────────────────────────
async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Anyone can call this — used to discover your chat ID on first setup."""
    uid = update.effective_user.id
    await update.message.reply_text(
        f"Your Telegram user ID is: `{uid}`\n\n"
        "Set this as the `OWNER_CHAT_ID` environment variable in Railway, "
        "then redeploy.",
        parse_mode="Markdown",
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("⛔ Not authorized. Send /myid to get your user ID.")
        return
    await update.message.reply_text(
        "💪 *WC Signup Bot*\n\n"
        "/book — schedule a class booking\n"
        "/list — view pending bookings\n"
        "/remove — cancel a pending booking",
        parse_mode="Markdown",
    )


# ── /book conversation ────────────────────────────────────────────────────────
async def cmd_book(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        await update.message.reply_text("⛔ Not authorized.")
        return ConversationHandler.END
    await update.message.reply_text("📅 Class date? (YYYY-MM-DD, e.g. 2026-07-05)")
    return DATE


async def got_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    try:
        datetime.strptime(txt, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD")
        return DATE
    ctx.user_data["date"] = txt
    await update.message.reply_text("⏰ Class time? (HH:MM, 24h, e.g. 08:00)")
    return TIME


async def got_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    try:
        datetime.strptime(txt, "%H:%M")
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use HH:MM")
        return TIME
    ctx.user_data["time"] = txt
    await update.message.reply_text(
        "🏋️ Category filter?\n"
        "Type part of the class name (e.g. `Hall A`) or /skip for any class at that time.",
        parse_mode="Markdown",
    )
    return CATEGORY


async def got_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["cat"] = update.message.text.strip()
    return await _finalize(update, ctx)


async def skip_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["cat"] = ""
    return await _finalize(update, ctx)


async def _finalize(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    d   = ctx.user_data["date"]
    t   = ctx.user_data["time"]
    cat = ctx.user_data.get("cat", "")

    target_dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
    win       = window_opens(target_dt)

    add_job(ctx.application, update.effective_chat.id, d, t, cat)

    cat_str = f"\nFilter: _{cat}_" if cat else ""
    await update.message.reply_text(
        f"✅ *Scheduled!*\n"
        f"Class: *{t} · {target_dt.strftime('%A, %d %b %Y')}*{cat_str}\n"
        f"Booking fires: *{win.strftime('%A %d %b at %H:%M')}*",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def abort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── /list ─────────────────────────────────────────────────────────────────────
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    bookings = load_schedule()
    if not bookings:
        await update.message.reply_text("No pending bookings.")
        return
    lines = ["*Pending bookings:*"]
    for b in bookings:
        cat = b.get("category_filter", "")
        win = datetime.fromisoformat(b["window_opens"]).strftime("%a %d %b %H:%M")
        lines.append(
            f"• *{b['class_time']}* on {b['class_date']}"
            + (f" · _{cat}_" if cat else "")
            + f"\n  ↳ books at {win}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /remove conversation ──────────────────────────────────────────────────────
REMOVE_CHOICE = 10


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update):
        return ConversationHandler.END
    bookings = load_schedule()
    if not bookings:
        await update.message.reply_text("No pending bookings to remove.")
        return ConversationHandler.END
    lines = ["Which booking to remove? Reply with the number:\n"]
    for i, b in enumerate(bookings, 1):
        cat = b.get("category_filter", "")
        lines.append(f"{i}. {b['class_date']} {b['class_time']}" + (f" · {cat}" if cat else ""))
    await update.message.reply_text("\n".join(lines))
    ctx.user_data["remove_list"] = bookings
    return REMOVE_CHOICE


async def got_remove_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    bookings = ctx.user_data.get("remove_list", [])
    try:
        b = bookings[int(update.message.text.strip()) - 1]
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid choice. Use /remove to try again.")
        return ConversationHandler.END

    try:
        scheduler.remove_job(b["job_id"])
    except Exception:
        pass
    save_schedule([x for x in load_schedule() if x.get("job_id") != b["job_id"]])

    cat = b.get("category_filter", "")
    await update.message.reply_text(
        f"❌ Removed: {b['class_date']} {b['class_time']}" + (f" · {cat}" if cat else "")
    )
    return ConversationHandler.END


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # Standalone commands
    app.add_handler(CommandHandler("myid",  cmd_myid))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list",  cmd_list))

    # /book conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("book", cmd_book)],
        states={
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_time)],
            CATEGORY: [
                CommandHandler("skip", skip_cat),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_category),
            ],
        },
        fallbacks=[CommandHandler("cancel", abort)],
    ))

    # /remove conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("remove", cmd_remove)],
        states={
            REMOVE_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_remove_choice)],
        },
        fallbacks=[CommandHandler("cancel", abort)],
    ))

    scheduler.start()
    restore_jobs(app)

    log.info("WC Signup Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

# WC Signup — Setup

Books CrossFit White City classes the moment the signup window opens.

## Architecture

A single long-lived Python process on Railway:

- **[bot.py](bot.py)** — Telegram bot (`/book`, `/list`, `/remove`) plus an APScheduler
  job store. When you schedule a class, it registers a one-shot job that fires
  5 minutes before the signup window, then sleeps to the exact second and POSTs
  the booking with up to 8 retries.
- **schedule.json** — pending bookings, so a restart doesn't lose the queue.
  Written at runtime as a JSON **list**; not committed.
- **Dockerfile / railway.toml / nixpacks.toml** — deployment. `railway.toml`
  selects nixpacks, so the Dockerfile is only used for local container runs.

There is no GitHub Actions path and no HTML dashboard. Both were removed on
2026-09-09; the earlier `book_class.py` CLI, `watchdog.py` cron poller and the
PowerShell scripts are in the Recycle Bin if you ever need them.

## Signup window rule

Classes open **48h** before start, except Sunday and Monday classes, which open
**72h** before (Thursday / Friday at the same time). Saturday is evenings only.
Encoded once, in `window_opens()`.

## Railway environment variables

| Variable | Value | Required |
|---|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather | yes |
| `OWNER_CHAT_ID` | your Telegram user ID — send `/myid` to the bot to get it | yes |
| `ARBOX_EMAIL` | `<ARBOX_EMAIL>` | yes |
| `ARBOX_PASSWORD` | `<ARBOX_PASSWORD>` | yes |
| `TZ` | `Asia/Jerusalem` | **yes — see below** |
| `ARBOX_WHITELABEL` | `HYPR-training` | defaulted |
| `ARBOX_BOXES_ID` | `59` | defaulted |
| `ARBOX_LOCATIONS_BOX_ID` | `48` | defaulted |
| `ARBOX_MEMBERSHIP_USER_ID` | `13705578` | defaulted |
| `DATA_DIR` | `/data` if you attach a Railway Volume | optional |

`TZ` is not optional. Arbox reports class times in Israel local time and
`bot.py` compares them with a naive `datetime.now()`. A Railway container runs
UTC, so without `TZ` every window is computed 2–3 hours late and the class is
already full by the time the bot fires.

Attach a Railway Volume and set `DATA_DIR=/data` if you want the pending queue
to survive redeploys — otherwise a deploy drops anything already scheduled.

## First run

1. Set the variables above in the Railway dashboard.
2. Deploy. Send `/myid` to the bot, put the number in `OWNER_CHAT_ID`, redeploy.
   Until `OWNER_CHAT_ID` is set, every command except `/myid` is refused.
3. `/book` → date (`YYYY-MM-DD`) → time (`HH:MM`) → category filter or `/skip`.
4. `/list` to check the queue, `/remove` to cancel one.

## Credentials

Never commit credentials. `config.json` is gitignored and is no longer read by
anything — `bot.py` takes its config from the environment only. Rotate the
Arbox password that is still sitting in that file in cleartext, then delete it.

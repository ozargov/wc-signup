# WC Signup — Setup

Books CrossFit White City classes the instant the signup window opens.
Runs entirely on GitHub Actions. No server, no monthly cost.

## The problem this has to solve

Popular classes here fill in **about five seconds**. So the booking request has
to arrive in the opening moment — not a few seconds after, and not a minute
late. Three measurements shape the whole design (all taken from `booking.log`):

| Measured fact | Consequence |
|---|---|
| A run on 2026-06-01 fired at 10:07 for an 08:00 window — over 2h late | The old per-booking cron was the failure. Never trust a timer to be punctual. |
| HTTP **516** `"Schedule Is Full"` is how a full class is reported | That is the signal to switch to the waiting list. |
| **HTTP 429** after ~4 requests in ~1.1s | Hammering harder than ~3 req/s is counter-productive. |

## How the timing works

You queue a class days ahead. A timer checks the queue every 30 minutes. When a
class's window is within 45 minutes, the job **starts early**, logs in, finds
the class, and only then sleeps — watching the clock — and fires at the exact
moment.

That split is the trick. GitHub's timer can run 5–15 minutes late, so it is
never asked to be punctual; it only has to start the job *before* the window.
The precision comes from the sleep inside the already-running job, which is
accurate to a few milliseconds.

Two further refinements for a five-second race:

- **The request is sent slightly early**, by half the round-trip time measured
  against the API moments before (clamped to 50–500ms). Sending *at* the window
  means arriving after it; this way the request lands as it opens.
- **The retry burst is paced at ~3 requests/second for 15 seconds**, staying
  under the observed 429 threshold, and backs off for 1.2s if it is rate-limited
  anyway.

## Signup window rule

Classes open **48h** ahead, except **Sunday and Monday** classes, which open
**72h** ahead — so Sunday's classes open on Thursday and Monday's on Friday, at
the same time of day.

Defined once, in `book_class.py:hours_before_for()`, and imported everywhere
else so it cannot drift.

## Waiting list

The waiting list is unlimited, so a full class is never a dead end. On HTTP 516
the script stops retrying **immediately** and joins the waiting list instead —
waitlists are ordered by join time, so every moment spent retrying a full class
costs a place. Arbox promotes you automatically when someone drops out.

`--no-waitlist` disables this if you ever want booking-only.

> **The waiting-list endpoint is not yet confirmed.** Arbox does not publish its
> API, and this account has never used the waiting list, so there is nothing in
> the logs to copy. `WAITLIST_CANDIDATES` in `book_class.py` tries six
> plausible paths in order and logs which one is accepted. Every candidate is
> waitlist-named — none can cancel or delete anything — and a wrong path just
> returns 404. Once a real request has been captured from the app, replace the
> list with the single correct path.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Booked |
| 5 | Could not book, and could not join the waiting list |
| 6 | Not booked, but on the waiting list (not treated as a failure) |

## Files

| File | Role |
|---|---|
| `bookings.json` | The queue: classes you want. Committed to the repo. |
| `bookings.py` | Adds to / removes from the queue; finds what's due and books it. |
| `book_class.py` | Logs in, finds the class, sleeps to the moment, bursts, falls back to the waiting list. |
| `.github/workflows/add-booking.yml` | The **Add booking** button you press from your phone. |
| `.github/workflows/book-due.yml` | The 30-minute timer. |

## Repository secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

| Secret | Value | Required |
|---|---|---|
| `ARBOX_EMAIL` | your HYPR app email | yes |
| `ARBOX_PASSWORD` | your HYPR app password | yes |
| `BOT_TOKEN` | Telegram bot token from @BotFather | only for notifications |
| `OWNER_CHAT_ID` | your Telegram user ID | only for notifications |
| `ARBOX_WHITELABEL` | `HYPR-training` | no — code default |
| `ARBOX_BOXES_ID` | `59` | no — code default |
| `ARBOX_LOCATIONS_BOX_ID` | `48` | no — code default |
| `ARBOX_MEMBERSHIP_USER_ID` | `13705578` | no — code default |

The last four are optional: an unset secret arrives as an empty string, which
`_env`/`_env_int` treat as "not set" and fall back to the code default. (An
earlier version did `int("")` on a missing secret and crashed.)

## Telegram notifications (optional, free)

Sending a message is a one-off web request, so it needs no always-on server.
Set `BOT_TOKEN` and `OWNER_CHAT_ID` and you get a message when a class is
queued, booked, waitlisted, or fails. Leave them unset and everything still
works — GitHub emails you when a run fails.

## Booking a class from your phone

GitHub app → your repo → **Actions** → **Add booking** → **Run workflow**:

- **action** — `add`, or `remove` to cancel one
- **class_date** — `2026-09-14`
- **class_time** — `08:00` (24h, Israel time)
- **category_filter** — `Hall A`, or blank for any class at that time

If the window is already open, it books immediately. Otherwise it waits in the
queue. `bookings.json` always shows what is pending.

## Running costs

Free — but on a **private** repo you get 2,000 Actions minutes a month, and this
uses roughly:

- the 30-minute timer: ~1,440 min/month
- each booking: up to 45 min (the job is sleeping, but sleeping still bills)

At 12 classes a month that is ~1,980 of 2,000 — too close. **Make the repo
public** and Actions minutes become unlimited and free. That is safe here:
credentials live in repository secrets, which stay encrypted and hidden even on
a public repo, and no credential is in the code. Just never commit
`config.json`.

## Known edges

- **60-day dormancy.** GitHub disables scheduled workflows in a repo with no
  activity for 60 days. Queuing a class commits a file, which counts, so normal
  use keeps it alive. After a long break, check the **Book due classes**
  workflow is still enabled.
- **Clock changes.** The window is computed in Israel local wall-clock time. On
  the two weekends a year when the clocks change, a window straddling the change
  could be an hour out. Book those manually.
- **Two classes at once.** Queued classes whose windows fall within 45 minutes
  of each other are booked one after another, earliest first; the second may
  fire a second or two late.
- **Plan limit.** The membership allows 12 classes a month. The bot does not
  track that — once the allowance is used up, bookings will simply fail and you
  will get a failure notification.

## Credentials

Never commit credentials. `config.json` is gitignored and nothing reads it any
more — everything comes from repository secrets. If that file still exists on
your PC with a password in it, rotate the password and delete the file.

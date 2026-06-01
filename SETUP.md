# WC Signup — GitHub Setup Guide

## How it works

The bot runs entirely on **GitHub Actions** — no laptop required.
Every 5 minutes, GitHub checks if any class booking window is opening soon.
When it is, GitHub books the class automatically, to the exact second.

You control everything from the HTML dashboard (or GitHub mobile app).

---

## Step 1 — Create a private GitHub repo

1. Go to [github.com/new](https://github.com/new)
2. Name it `wc-signup` (or anything you like)
3. Set it to **Private**
4. Click **Create repository**

---

## Step 2 — Upload the files

Upload all of these files to the root of the repo:

- `book_class.py`
- `watchdog.py`
- `schedule.json`
- `WC Signup bot new.html`
- `.github/workflows/wc-signup.yml`

**Tip:** You can drag-and-drop files on github.com, but the `.github/workflows/` folder needs to be created first.
The easiest way is to use GitHub Desktop or run these commands in the folder:

```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/wc-signup.git
git add .
git commit -m "Initial setup"
git push -u origin main
```

---

## Step 3 — Add GitHub Secrets (your credentials)

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each of the following:

| Secret name               | Value                          |
|---------------------------|--------------------------------|
| `ARBOX_EMAIL`             | Your HYPR app email            |
| `ARBOX_PASSWORD`          | Your HYPR app password         |
| `ARBOX_WHITELABEL`        | `HYPR-training`                |
| `ARBOX_BOXES_ID`          | `59`                           |
| `ARBOX_LOCATIONS_BOX_ID`  | `48`                           |
| `ARBOX_MEMBERSHIP_USER_ID`| `13705578`                     |

---

## Step 4 — Enable GitHub Actions

1. Go to your repo → **Actions** tab
2. If prompted, click **I understand my workflows, go ahead and enable them**
3. You should see **WC Signup** in the workflow list

---

## Step 5 — Connect the dashboard

1. Open `WC Signup bot new.html` in your browser
2. Go to the **Settings** tab
3. Enter your GitHub username, repo name (`wc-signup`), and a Personal Access Token

**Creating a Personal Access Token:**
1. Go to [github.com/settings/tokens/new](https://github.com/settings/tokens/new?scopes=repo,workflow)
2. Give it a name like `wc-signup-dashboard`
3. Check **repo** and **workflow** scopes
4. Click **Generate token** — copy it immediately (you won't see it again)
5. Paste it in the dashboard Settings tab

Click **Test connection** to verify, then **Save GitHub settings**.

---

## Step 6 — Schedule a class

1. Open the dashboard → **Schedule** tab
2. Navigate to the day you want (e.g., Wednesday)
3. Check the class you want (it should show "Opens in Xh" — meaning the window isn't open yet)
4. Click **Schedule selected**
5. The class is now queued in `schedule.json` on GitHub

GitHub Actions will automatically book it the moment the 48h signup window opens.

---

## Controlling from your phone

Install the **GitHub** mobile app:
- [iOS App Store](https://apps.apple.com/app/github/id1477376905)
- [Google Play](https://play.google.com/store/apps/details?id=com.github.android)

**To book manually from the phone:**
1. Open GitHub app → your `wc-signup` repo
2. Tap **Actions** → **WC Signup**
3. Tap **Run workflow**
4. Fill in: class date (e.g., `2026-06-10`), class time (e.g., `08:00`), category filter (e.g., `W.O.D Hall A`)
5. Tap **Run workflow** — GitHub fires it immediately and waits for the signup window

**To check results:**
- Actions tab shows each run with ✅ success or ❌ failure
- Tap any run to see the full log, including "BOOKING CONFIRMED"

---

## Timing note

The watchdog runs every 5 minutes. When a booking window is due in the next 10 minutes, GitHub starts the job early and the Python script sleeps precisely to the exact second. This means bookings fire within a few seconds of the window opening — well within the 1-2 minute fill time for popular classes.

GitHub Actions timing is reliable at 5 AM UTC (8 AM Israel time) as it's off-peak. If a class ever misses, you can always trigger manually from the phone.

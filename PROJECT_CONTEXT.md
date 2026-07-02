# Project Context

This file is the durable handoff note for continuing this project from another
computer or a new Codex thread. Do not put secrets, API keys, tokens, or private
account identifiers in this file.

## What This Project Is

`stockanalysis` is a private A-share trading review assistant. It provides:

- A mobile-first local web UI.
- Telegram bot integration.
- Screenshot upload and AI-assisted extraction.
- Manual trade and watchlist management.
- Daily watchlist reports.
- Sector rotation reports for AI infrastructure subsectors.
- SQLite persistence.

The app is a review and discipline tool. It does not place trades and does not
connect to broker or Tonghuashun accounts.

## Current Runtime

The active Mac runtime uses a deployed copy under:

```text
~/Library/Application Support/StockAnalysis
```

Important runtime files:

```text
.env
data/trade_review.sqlite3
data/uploads/
data/logs/
```

The source repo lives under:

```text
/Users/charleszhang/Documents/Codex/2026-06-03/new-chat/outputs/trade-review-app
```

The macOS launchd service is:

```text
com.charleszhang.stockanalysis
```

The local web app normally runs at:

```text
http://127.0.0.1:8765
```

## Current Providers

Current intended configuration:

```text
TEXT_AI_PROVIDER=deepseek
VISION_AI_PROVIDER=local
MARKET_DATA_PROVIDER=tushare
DAILY_REPORT_TIME=17:30
```

DeepSeek is used for text reviews and daily reports.
Tushare is the primary market data source, with AKShare fallback.
Local LM Studio is used for vision/screenshot recognition on the Mac runtime.

Do not commit `.env`. It contains Telegram, DeepSeek, Tushare, and other private
configuration.

## Daily Watchlist Report Behavior

Daily Telegram reports:

- Run after `DAILY_REPORT_TIME`, currently `17:30`.
- Are skipped on non-China-market trading days.
- Use Tushare trade calendar when available.
- Fall back to market K-line date checks if the trade calendar call fails.
- Use the last valid trading report for the "previous focus review", not the
  previous calendar day.
- Skip stale weekend/holiday reports whose market data date does not match the
  report date.
- Store selected focus stocks in structured `market_data_json.focus_items` for
  more reliable future reviews.
- Include concept/sector labels in selected stock titles using the format:

```text
Name（Code｜Concept）
```

The scheduler was changed from exact-minute matching to "send after scheduled
time if today's report has not been sent", so missing the exact `17:30` minute
should not prevent sending.

## Telegram Bot Rules

The owner/admin user is controlled by `TELEGRAM_ALLOWED_USER_ID`.

Non-owner users are subscriber-only:

- `/start` or any message subscribes them.
- `/stop` unsubscribes them.
- They cannot use admin commands, upload screenshots for processing, or trigger
  reports manually.

Daily reports are broadcast to:

- `TELEGRAM_REPORT_CHAT_ID`
- active rows in the `telegram_subscribers` table
- optional IDs listed in `TELEGRAM_SUBSCRIBER_CHAT_IDS`

Do not run the same Telegram bot token actively on two machines at the same
time. Telegram long polling can conflict and cause missed or duplicated updates.

## Important Implementation Notes

- Main app: `app.py`
- Web assets: `public/`
- macOS launchd template: `scripts/com.charleszhang.stockanalysis.plist`
- Mac redeploy script: `scripts/deploy_launchd.sh`
- OCR helper: `scripts/vision_ocr.swift`

Market data:

- Tushare daily data is adjusted locally with `adj_factor` to match forward
  adjusted price/MA behavior expected from Tonghuashun-style charts.
- Percent change is calculated locally from adjusted close values.
- Watch reports fetch market contexts concurrently.

Reports:

- `daily_stock_reports.market_data_json.type == "watch_report"` identifies
  normal watchlist reports.
- `type == "sector_rotation"` identifies sector rotation reports.
- Watch reports store coverage, previous focus review, focus items, watch items,
  and compact market context.

## Deploy And Operations

After changing source code on the Mac, deploy to the running copy and restart:

```sh
./scripts/deploy_launchd.sh
```

The deploy script preserves runtime `.env`, SQLite database, uploads, and logs.

Useful checks:

```sh
curl -s http://127.0.0.1:8765/api/health
launchctl print gui/501/com.charleszhang.stockanalysis
tail -n 100 "$HOME/Library/Application Support/StockAnalysis/data/logs/launchd.stdout.log"
tail -n 100 "$HOME/Library/Application Support/StockAnalysis/data/logs/launchd.stderr.log"
```

Typical report resend from the runtime copy:

```sh
cd "$HOME/Library/Application Support/StockAnalysis"
python3 -c "import app,json; app.init_db(); r=app.generate_watch_report('YYYY-MM-DD'); print(json.dumps(app.telegram_broadcast_report(r), ensure_ascii=False))"
```

## Git And New Computer Handoff

GitHub repo:

```text
https://github.com/8pt-cool/stockanalysis.git
```

On a new machine:

```sh
git clone https://github.com/8pt-cool/stockanalysis.git
cd stockanalysis
python3 -m pip install -r requirements.txt
```

Then migrate runtime-only files separately:

```text
.env
data/trade_review.sqlite3
data/uploads/
```

If using macOS launchd on the new machine, run:

```sh
./scripts/deploy_launchd.sh
```

For Git pushes on macOS:

```sh
git config --global credential.helper osxkeychain
git push origin main
```

Use a GitHub PAT with write access when prompted. Do not put PATs in remotes,
scripts, `.env`, README, or this context file.

## Recent Decisions And Fixes

- Daily reports are skipped on non-China-market trading days.
- Reports use last valid trading report for focus review across weekends and
  holidays.
- Report titles include concept/sector labels consistently.
- Daily scheduler sends after the scheduled time if today's report was not
  already sent, instead of requiring exact minute matching.
- Logs are flushed for launchd visibility.
- The macOS deploy script no longer overwrites runtime data.

## How To Continue In A New Codex Thread

Tell Codex:

```text
Please read README.md and PROJECT_CONTEXT.md first, then continue this project.
```

Then ask for the next task. The repo docs plus runtime `.env` and SQLite data
should be enough to recover the project context without relying on old chat
history.

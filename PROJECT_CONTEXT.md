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
- Private position management and owner-only position reports.
- Daily watchlist reports.
- Sector rotation reports for AI infrastructure subsectors.
- A-share hot sector reports.
- 20-day 3L momentum model reports.
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
VISION_AI_PROVIDER=minimax
MARKET_DATA_PROVIDER=auto
DAILY_REPORT_TIME=17:30
POSITION_REPORT_TIME=17:35
```

DeepSeek is used for text reviews and daily reports.
Market data tries Wudao A-share MCP first, then Tushare, then AKShare.
MiniMax is the intended cloud vision provider for screenshot recognition on the
VPS. Local LM Studio can still be used for vision/screenshot recognition on the
Mac runtime, but it is too slow and is not available on the small VPS.

Do not commit `.env`. It contains Telegram, DeepSeek, Tushare, Wudao, and other
private configuration.

Wudao MCP:

- Stable URL: `https://stock.quicktiny.cn/api/mcp`
- Use regular HTTP/stateless JSON-RPC with `Authorization: Bearer ...`
- Do not use the stream URL unless the client explicitly supports it.
- The Codex global MCP server name is `wudao`, but the app also has its own
  direct JSON-RPC client so Docker/cloud can call Wudao without Codex.

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
- Docker cloud deployment guide: `DEPLOY_DOCKER.md`
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
- `type == "hot_sector_snapshot"` identifies hot sector reports.
- `type == "momentum_snapshot"` identifies 20-day momentum model reports.
- `type == "position_report"` identifies owner-only private position reports.
- Watch reports store coverage, previous focus review, focus items, watch items,
  and compact market context.

Screenshot modes:

- `auto`: let the model decide the screenshot type.
- `intraday_sb`: intraday chart screenshots with S/B markers.
- `broker_records`: broker app trade record lists.
- `watchlist_snapshot`: watchlist screenshots with stock names, codes, percent
  changes, and latest prices.
- `position_snapshot`: broker app position list screenshots.

Private positions:

- Caption Telegram photos with `持仓` to force position mode.
- Position imports create private position rows, not public watchlist rows.
- Position reports are owner-only and must never be broadcast to ordinary
  Telegram subscribers.
- Position quantity can be derived from market value when screenshots include
  value and price but not quantity.
- Position import uses cached stock codes and avoids slow fund lookup.

Hot sector reports:

- API: `POST /api/hot-sector-report`
- Telegram owner command: `/hot_sectors`
- Web button: `生成热点板块分析`
- Prefer Wudao MCP tools `hot_sectors`, `theme_intraday_capital`,
  `concept_ranking`, and `market_overview`.
- Fall back to the older AkShare board snapshot path if Wudao fails.
- Treat Wudao `volumeRatio` as volume/amount ratio evidence. Avoid saying
  breadth is confirmed unless `up_ratio` is present.

## 3L 20-Day Momentum Model

Source PDF: `12 3.3动量模型.pdf`.

Target model from the PDF:

1. Use 20 trading days as the momentum period.
2. Sort all listed A-share companies by 20-day gain.
3. Take the top 700 stocks as the 20-day momentum pool.
4. Exclude stocks listed for fewer than 20 days.
5. Keep only stocks with institutional holding >= 2% or northbound holding >=
   0.5%.
6. Group remaining stocks by sector/industry.
7. Compute `listed_ratio = listed_count / industry_member_count`.
8. Compute `momentum_score = listed_count * listed_ratio`.
9. Score > 1 means the sector has a meaningful momentum effect.
10. Score >= 7 means the sector may be near a climax and requires volume/price
    timing confirmation.

Current implementation:

- API: `POST /api/momentum-report`
- Telegram owner command: `/momentum`
- Web button: `生成20日动量模型`
- Function: `generate_momentum_report()`
- Data source: Wudao MCP.
- Current approximation uses `stock_rank(type=gainers_20d)` plus
  `stock_screener` to fill industry fields.
- Wudao currently limits `stock_rank` to 200 rows, so this is a Top200
  approximation, not the PDF's Top700 model.
- Institutional/northbound holding filters are not enabled yet because the
  current exposed Wudao tools do not provide those filter fields.
- To protect Wudao free quota, do not query industry member counts one by one.
  Use `sector_analysis(source=industry, period=20)` as a single-call source for
  available `stockCount` values. Industries not covered by that snapshot should
  show listed counts but should not force a momentum score.
- The Wudao free tier is 50 calls/day and can be exhausted quickly during
  debugging.

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

## Cloud VPS Handoff

The Docker deployment files are:

```text
Dockerfile
docker-compose.yml
.env.docker.example
DEPLOY_DOCKER.md
```

Use a non-mainland-China region for Telegram reliability.
On cloud VPS, set `APP_HOST=0.0.0.0`, mount `./data:/app/data`, and keep
runtime secrets in `.env`.

The Docker version disables macOS local OCR by default. LM Studio/local vision is
not expected to work on a small VPS; use MiniMax or another cloud vision
provider for screenshot recognition on the VPS.

Current VPS notes:

- Host: Tencent Cloud VPS.
- SSH: `ssh -p 2222 -i ~/.ssh/id_ed25519 ubuntu@150.109.24.81`
- App directory: `/home/ubuntu/apps/stockanalysis`
- Docker service/container: `stockanalysis`
- Web port: `8765`

## Recent Decisions And Fixes

- MiniMax vision provider was added for cloud screenshot recognition.
- Docker deployment support was added and the VPS Docker service is now the
  practical always-on runtime.
- Private position screenshot import and private position reports were added.
- Position imports derive quantity from market value when possible, use cached
  stock codes, and avoid slow fund lookup.
- Daily reports are skipped on non-China-market trading days.
- Reports use last valid trading report for focus review across weekends and
  holidays.
- Report titles include concept/sector labels consistently.
- Daily scheduler sends after the scheduled time if today's report was not
  already sent, instead of requiring exact minute matching.
- Logs are flushed for launchd visibility.
- The macOS deploy script no longer overwrites runtime data.
- Wudao MCP is configured and `MARKET_DATA_PROVIDER=auto` on the cloud VPS.
- Hot sector reports use Wudao first.
- Added `/momentum` and `/api/momentum-report` for the 3L 20-day momentum model.

## How To Continue In A New Codex Thread

Tell Codex:

```text
Please read README.md and PROJECT_CONTEXT.md first, then continue this project.
```

Then ask for the next task. The repo docs plus runtime `.env` and SQLite data
should be enough to recover the project context without relying on old chat
history.

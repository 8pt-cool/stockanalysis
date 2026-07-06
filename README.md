# Trade Review Assistant

Mac mini private trading review assistant with a mobile web UI, SQLite storage,
Telegram bot integration, and AI hooks for screenshot extraction and review.

This app is intentionally a review and discipline tool. It does not place trades
and does not connect to broker or Tonghuashun accounts.

For cross-computer or new-thread handoff notes, read `PROJECT_CONTEXT.md`.
For cloud VPS deployment, read `DEPLOY_DOCKER.md`.

## Quick Start

```sh
cd work/trade-review-app
cp .env.example .env
python3 app.py
```

Open:

```text
http://127.0.0.1:8765
```

## Environment

Set these values in `.env` or your shell:

```text
APP_HOST=127.0.0.1
APP_PORT=8765
APP_SECRET=change-this-password
DATABASE_PATH=./data/trade_review.sqlite3

OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

AI_PROVIDER=deepseek
TEXT_AI_PROVIDER=deepseek
VISION_AI_PROVIDER=local
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

LOCAL_AI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_AI_API_KEY=lm-studio
LOCAL_AI_MODEL=google/gemma-4-31b
LOCAL_AI_VISION_MODEL=google/gemma-4-31b
LOCAL_AI_TIMEOUT_SECONDS=300
LOCAL_AI_SUPPORTS_IMAGES=true

TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
TELEGRAM_REPORT_CHAT_ID=
TELEGRAM_SUBSCRIBER_CHAT_IDS=
DAILY_REPORT_TIME=17:30

MARKET_DATA_PROVIDER=tushare
MARKET_LOOKBACK_DAYS=260
MOMENTUM_DATA_PROVIDER=tushare
WUDAO_API_KEY=
WUDAO_MCP_URL=https://stock.quicktiny.cn/api/mcp
WUDAO_CACHE_ENABLED=true
WUDAO_CACHE_TTL_SECONDS=21600
WUDAO_KLINE_CACHE_TTL_SECONDS=86400
WUDAO_TRADING_CALENDAR_CACHE_TTL_SECONDS=604800
WUDAO_REPORT_CACHE_ENABLED=true
DAILY_BAR_CACHE_ENABLED=true
DAILY_BAR_FETCH_TTL_SECONDS=21600
```

Set `TEXT_AI_PROVIDER=deepseek` to use DeepSeek for text reviews and reports.
Set `VISION_AI_PROVIDER=local` to keep screenshot recognition on LM Studio.
LM Studio usually serves at `http://127.0.0.1:1234/v1`.

If `AI_PROVIDER=openai` and `OPENAI_API_KEY` is missing, AI endpoints return a
useful placeholder instead of failing the whole app.

`google/gemma-4-31b` can be used for screenshot recognition when your LM Studio
build exposes it with vision input enabled. Keep `LOCAL_AI_SUPPORTS_IMAGES=true`
for that setup.

If `TELEGRAM_BOT_TOKEN` is missing, the bot is disabled and the web app still
works.

## Current MVP

- Mobile-first PWA web app
- SQLite schema and migrations
- Manual trade entry
- Screenshot upload and local persistence
- AI screenshot extraction hook
- AI trade review hook
- Watchlist CRUD
- AKShare daily K-line context for trade reviews
- Daily watchlist report generation hook
- AI sector rotation report for sector/top-ranked watchlist screenshots
- Telegram bot polling:
  - `/start`
  - `/today`
  - `/list`
  - `/watch 600519 贵州茅台`
  - `/report`
  - `/sector`
  - photo upload
- Optional daily Telegram watchlist report with `TELEGRAM_REPORT_CHAT_ID` and
  `DAILY_REPORT_TIME`; automatic reports are skipped on non-China-market
  trading days

## Screenshot Modes

The upload page supports three recognition modes:

- `auto`: let the model decide the screenshot type.
- `intraday_sb`: intraday chart screenshots with S/B markers.
- `broker_records`: broker app trade record lists.
- `watchlist_snapshot`: watchlist screenshots with stock names, codes, percent
  changes, and latest prices.

Sector/top-ranked screenshots, such as AI infrastructure subsector maps, are
stored with `sector_name` and `sector_rank`. Use `/sector` in Telegram or
`POST /api/sector-report` to generate a sector rotation report focused on
weak-but-stabilizing subsectors and low-absorption watch candidates.

Use `/momentum` in Telegram or `POST /api/momentum-report` to generate the
20-day 3L momentum model. By default it uses local Tushare daily bars stored in
SQLite and does not consume Wudao quota.

For Telegram photo uploads, add a caption to force the mode:

```text
日内
```

or:

```text
券商成交
```

or:

```text
自选
```

The model returns JSON-like structured output for manual confirmation. It still
does not auto-save recognized trades into the trade table.

On macOS, table-like screenshots first use the local Vision OCR fast path when
`LOCAL_OCR_ENABLED=true`. If OCR cannot extract a usable watchlist/table
structure, the app falls back to the configured vision model.

## Market Data

Install dependencies:

```sh
python3 -m pip install -r requirements.txt
```

With `MARKET_DATA_PROVIDER=tushare`, trade reviews fetch recent A-share daily
K-line data with Tushare and fall back to AKShare if Tushare is unavailable.
Set `TUSHARE_TOKEN` in `.env` before using Tushare.

With `MARKET_DATA_PROVIDER=wudao`, trade reviews fetch front-adjusted daily
K-line data from the Wudao A-share MCP and fall back to Tushare, then AKShare.
With `MARKET_DATA_PROVIDER=auto`, the app tries Wudao first, then Tushare, then
AKShare. Hot sector reports automatically try Wudao when `WUDAO_API_KEY` is set.

Wudao has daily quota limits. The app caches successful Wudao tool responses in
SQLite by default: normal tools for 6 hours, K-line responses for 24 hours, and
trading-calendar checks for 7 days. Re-running the same hot-sector or momentum
report for the same trading day reuses the stored report text instead of calling
Wudao again. If quota is tight, keep `MARKET_DATA_PROVIDER=tushare` for normal
per-stock reports and reserve Wudao for `/hot_sectors` and `/momentum`.

Successful Tushare K-line responses are also persisted to SQLite in
`daily_bars`. For the same stock/date/lookback window, the app reuses local bars
after a recent fetch attempt, even if the upstream data has not updated to the
requested report date yet. `DAILY_BAR_FETCH_TTL_SECONDS` controls that retry
window and defaults to 6 hours. The default `MARKET_LOOKBACK_DAYS=260` keeps
about one trading year of daily bars available for later analysis.

The 20-day momentum model defaults to `MOMENTUM_DATA_PROVIDER=tushare`: it uses
`daily_bars` rows from the `tushare_raw` provider plus local stock metadata to
compute the Top700 industry momentum pool locally. Industry grouping prefers
Tushare index-classify mappings (`tushare_industry`), then 同花顺行业
(`ths_industry`), then `stock_basic.industry`. The current VPS token can use
`SW2021`; Tushare `THS`/`ths_index` requires additional interface access. Set
`MOMENTUM_DATA_PROVIDER=wudao` only when Wudao quota is available and the older
Wudao Top200 approximation is desired.

The app calculates:

- OHLC and daily percent change
- MA5, MA10, MA20
- volume versus 20-day average
- each trade price position inside the day range

The AI receives this compact market summary instead of raw K-line tables.

## Git Push

This Mac is configured to use the macOS Keychain for Git credentials:

```sh
git config --global credential.helper osxkeychain
```

Normal pushes should use:

```sh
git push origin main
```

If GitHub starts asking for credentials again, use a PAT with write access to
`8pt-cool/stockanalysis`, then run one normal `git push`. Git will store the
credential in Keychain for later pushes.

## Auto Start on macOS

This app can run at login and restart automatically with `launchd`.

Template:

```text
scripts/com.charleszhang.stockanalysis.plist
```

Install:

```sh
mkdir -p ~/Library/LaunchAgents
cp scripts/com.charleszhang.stockanalysis.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.charleszhang.stockanalysis.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.charleszhang.stockanalysis.plist
launchctl start com.charleszhang.stockanalysis
```

After code changes, redeploy and restart with:

```sh
./scripts/deploy_launchd.sh
```

The deploy script updates code and service files while preserving the runtime
`.env`, SQLite database, uploads, and logs under `~/Library/Application Support/StockAnalysis`.

Useful checks:

```sh
launchctl list | grep stockanalysis
curl -s http://127.0.0.1:8765/api/health
```

## Privacy Notes

- Do not upload screenshots that include account numbers, ID numbers, phone
  numbers, or broker login information.
- Original screenshots are stored under `data/uploads`. You can delete them
  from disk if you only want to keep structured records.
- Telegram messages and uploaded images pass through Telegram. AI image analysis
  sends the image to the configured AI provider.

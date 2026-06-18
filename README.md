# Trade Review Assistant

Mac mini private trading review assistant with a mobile web UI, SQLite storage,
Telegram bot integration, and AI hooks for screenshot extraction and review.

This app is intentionally a review and discipline tool. It does not place trades
and does not connect to broker or Tonghuashun accounts.

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
MARKET_LOOKBACK_DAYS=90
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
  `DAILY_REPORT_TIME`

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

## Privacy Notes

- Do not upload screenshots that include account numbers, ID numbers, phone
  numbers, or broker login information.
- Original screenshots are stored under `data/uploads`. You can delete them
  from disk if you only want to keep structured records.
- Telegram messages and uploaded images pass through Telegram. AI image analysis
  sends the image to the configured AI provider.

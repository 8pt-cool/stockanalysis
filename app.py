#!/usr/bin/env python3
import base64
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"


def load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8765"))
APP_SECRET = os.getenv("APP_SECRET", "change-this-password")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "trade_review.sqlite3")))
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()
TEXT_AI_PROVIDER = os.getenv("TEXT_AI_PROVIDER", AI_PROVIDER).strip().lower()
VISION_AI_PROVIDER = os.getenv("VISION_AI_PROVIDER", "local").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LOCAL_AI_BASE_URL = os.getenv("LOCAL_AI_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
LOCAL_AI_API_KEY = os.getenv("LOCAL_AI_API_KEY", "local")
LOCAL_AI_MODEL = os.getenv("LOCAL_AI_MODEL", "google/gemma-4-31b")
LOCAL_AI_VISION_MODEL = os.getenv("LOCAL_AI_VISION_MODEL", LOCAL_AI_MODEL)
LOCAL_AI_TIMEOUT_SECONDS = int(os.getenv("LOCAL_AI_TIMEOUT_SECONDS", "300"))
LOCAL_AI_SUPPORTS_IMAGES = os.getenv("LOCAL_AI_SUPPORTS_IMAGES", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID", "")
TELEGRAM_REPORT_CHAT_ID = os.getenv("TELEGRAM_REPORT_CHAT_ID", "")
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "15:30")
MARKET_DATA_PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "akshare").strip().lower()
MARKET_LOOKBACK_DAYS = int(os.getenv("MARKET_LOOKBACK_DAYS", "90"))
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
LOCAL_OCR_ENABLED = os.getenv("LOCAL_OCR_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LOCAL_OCR_TIMEOUT_SECONDS = int(os.getenv("LOCAL_OCR_TIMEOUT_SECONDS", "20"))


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


def db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def today_str():
    return dt.date.today().isoformat()


def yesterday_str():
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def init_db():
    ensure_dirs()
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
              id TEXT PRIMARY KEY,
              trade_date TEXT NOT NULL,
              stock_code TEXT NOT NULL,
              stock_name TEXT,
              side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
              price REAL NOT NULL,
              quantity INTEGER NOT NULL,
              amount REAL NOT NULL,
              reason TEXT,
              source TEXT NOT NULL DEFAULT 'manual',
              screenshot_id TEXT,
              confirmed INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS screenshots (
              id TEXT PRIMARY KEY,
              file_path TEXT NOT NULL,
              image_type TEXT,
              ocr_json TEXT,
              imported_at TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_reviews (
              id TEXT PRIMARY KEY,
              trade_date TEXT NOT NULL UNIQUE,
              summary TEXT,
              mistakes TEXT,
              lessons TEXT,
              emotion_score INTEGER,
              discipline_score INTEGER,
              ai_review TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watchlist (
              id TEXT PRIMARY KEY,
              stock_code TEXT NOT NULL UNIQUE,
              stock_name TEXT,
              reason TEXT,
              sector_name TEXT,
              sector_rank INTEGER,
              strategy_type TEXT,
              support_price REAL,
              resistance_price REAL,
              target_buy_min REAL,
              target_buy_max REAL,
              stop_loss REAL,
              max_position TEXT,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_stock_reports (
              id TEXT PRIMARY KEY,
              report_date TEXT NOT NULL,
              stock_code TEXT,
              stock_name TEXT,
              market_data_json TEXT,
              ai_summary TEXT,
              signal_level TEXT,
              risk_level TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stock_code_cache (
              stock_name TEXT PRIMARY KEY,
              stock_code TEXT NOT NULL,
              source TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(screenshots)")
        }
        if "imported_at" not in existing_columns:
            conn.execute("ALTER TABLE screenshots ADD COLUMN imported_at TEXT")
        watch_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(watchlist)")
        }
        if "sector_name" not in watch_columns:
            conn.execute("ALTER TABLE watchlist ADD COLUMN sector_name TEXT")
        if "sector_rank" not in watch_columns:
            conn.execute("ALTER TABLE watchlist ADD COLUMN sector_rank INTEGER")


def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def parse_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return json.loads(raw.decode("utf-8") or "{}")
    return urllib.parse.parse_qs(raw.decode("utf-8"))


def require_secret(handler):
    secret = handler.headers.get("X-App-Secret", "")
    return secret == APP_SECRET


def json_response(handler, data, status=200):
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def error_response(handler, message, status=400):
    json_response(handler, {"ok": False, "error": message}, status)


def ai_complete(messages, image_b64=None, image_mime="image/jpeg"):
    provider = VISION_AI_PROVIDER if image_b64 else TEXT_AI_PROVIDER
    if provider in ("local", "openai-compatible", "openai_compatible", "ollama"):
        return local_chat_completions(messages, image_b64=image_b64, image_mime=image_mime)
    if provider == "deepseek":
        if image_b64:
            return {
                "text": "DeepSeek 当前配置用于文本分析；图片识别请使用 VISION_AI_PROVIDER=local。"
            }
        return deepseek_chat_completions(messages)
    return openai_responses(messages, image_b64=image_b64, image_mime=image_mime)


def chat_completion_text(base_url, api_key, model, messages, timeout=120, provider_name="AI"):
    if not api_key:
        return {"text": f"{provider_name} 未配置：请在 .env 设置 API Key。"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "\n\n".join(messages)}],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"text": f"{provider_name} 请求失败：HTTP {exc.code} {detail}"}
    except Exception as exc:
        return {"text": f"{provider_name} 请求失败：{exc}"}
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = json.dumps(data, ensure_ascii=False)
    return {"text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)}


def deepseek_chat_completions(messages):
    return chat_completion_text(
        DEEPSEEK_BASE_URL,
        DEEPSEEK_API_KEY,
        DEEPSEEK_MODEL,
        messages,
        timeout=120,
        provider_name="DeepSeek",
    )


def openai_responses(messages, image_b64=None, image_mime="image/jpeg"):
    if not OPENAI_API_KEY:
        return {
            "text": "AI 未配置：请在 .env 设置 OPENAI_API_KEY。当前记录已保存，可稍后重新生成分析。"
        }

    content = []
    for message in messages:
        content.append({"type": "input_text", "text": message})
    if image_b64:
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{image_mime};base64,{image_b64}",
            }
        )

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"text": f"AI 请求失败：HTTP {exc.code} {detail}"}
    except Exception as exc:
        return {"text": f"AI 请求失败：{exc}"}

    text_chunks = []
    for item in data.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                text_chunks.append(part.get("text", ""))
    return {"text": "\n".join(text_chunks).strip() or json.dumps(data, ensure_ascii=False)}


def local_chat_completions(messages, image_b64=None, image_mime="image/jpeg"):
    if image_b64 and not LOCAL_AI_SUPPORTS_IMAGES:
        return {
            "text": (
                "当前本地模型配置为文本模式，不能直接识别截图。\n"
                "你可以继续用它生成交易复盘和自选股日报；如果要识别截图，请换成支持视觉输入的本地模型，"
                "并设置 LOCAL_AI_SUPPORTS_IMAGES=true。"
            )
        }

    text = "\n\n".join(messages)
    content = [{"type": "text", "text": text}]
    if image_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image_mime};base64,{image_b64}"},
            }
        )

    payload = {
        "model": LOCAL_AI_VISION_MODEL if image_b64 else LOCAL_AI_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        f"{LOCAL_AI_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {LOCAL_AI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LOCAL_AI_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"text": f"本地 AI 请求失败：HTTP {exc.code} {detail}"}
    except Exception as exc:
        return {
            "text": (
                f"本地 AI 请求失败：{exc}\n"
                f"请确认 LOCAL_AI_BASE_URL={LOCAL_AI_BASE_URL} 可访问，并且模型 {LOCAL_AI_MODEL} 已启动。"
            )
        }

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = json.dumps(data, ensure_ascii=False)
    return {"text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)}


def create_trade(payload):
    side = payload.get("side", "buy")
    price = float(payload.get("price") or 0)
    quantity = int(payload.get("quantity") or 0)
    amount = float(payload.get("amount") or price * quantity)
    trade = {
        "id": str(uuid.uuid4()),
        "trade_date": payload.get("trade_date") or today_str(),
        "stock_code": str(payload.get("stock_code") or "").strip(),
        "stock_name": str(payload.get("stock_name") or "").strip(),
        "side": side,
        "price": price,
        "quantity": quantity,
        "amount": amount,
        "reason": str(payload.get("reason") or "").strip(),
        "source": payload.get("source") or "manual",
        "screenshot_id": payload.get("screenshot_id"),
        "confirmed": 1 if payload.get("confirmed", True) else 0,
        "created_at": now_iso(),
    }
    if not trade["stock_code"]:
        raise ValueError("stock_code is required")
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    if price <= 0 or quantity <= 0:
        raise ValueError("price and quantity must be positive")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO trades
            (id, trade_date, stock_code, stock_name, side, price, quantity, amount,
             reason, source, screenshot_id, confirmed, created_at)
            VALUES
            (:id, :trade_date, :stock_code, :stock_name, :side, :price, :quantity,
             :amount, :reason, :source, :screenshot_id, :confirmed, :created_at)
            """,
            trade,
        )
    return trade


def list_trades(trade_date=None):
    query = "SELECT * FROM trades"
    params = []
    if trade_date:
        query += " WHERE trade_date = ?"
        params.append(trade_date)
    query += " ORDER BY trade_date DESC, created_at DESC"
    with db() as conn:
        return [row_to_dict(row) for row in conn.execute(query, params)]


def list_screenshots(limit=20):
    with db() as conn:
        rows = [
            row_to_dict(row)
            for row in conn.execute(
                """
                SELECT id, image_type, ocr_json, imported_at, created_at
                FROM screenshots
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        ]
    for row in rows:
        row["ocr_json"] = enrich_broker_result_codes(
            normalize_broker_result(row.get("ocr_json") or "")
        )
        row["ocr_json"] = normalize_watchlist_result(row.get("ocr_json") or "")
    return rows


def get_screenshot(screenshot_id):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM screenshots WHERE id = ?", (screenshot_id,)
        ).fetchone()
    return row_to_dict(row) if row else None


def latest_screenshot(pending_only=False):
    query = "SELECT * FROM screenshots"
    params = []
    if pending_only:
        query += " WHERE imported_at IS NULL"
    query += " ORDER BY created_at DESC LIMIT 1"
    with db() as conn:
        row = conn.execute(query, params).fetchone()
    return row_to_dict(row) if row else None


def list_watchlist():
    with db() as conn:
        return [
            row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM watchlist WHERE active = 1 ORDER BY created_at DESC"
            )
        ]


def compact_stock_code(stock_code):
    return str(stock_code or "").strip().split(".")[0]


def yyyymmdd(value):
    return str(value or "").replace("-", "")


def market_prefixed_code(stock_code):
    code = compact_stock_code(stock_code)
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def normalize_tx_frame(frame):
    if frame is None or frame.empty:
        return frame
    renamed = frame.rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "amount": "成交量",
        }
    )
    return renamed


def tushare_ts_code(stock_code):
    code = compact_stock_code(stock_code)
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def normalize_tushare_frame(frame):
    if frame is None or frame.empty:
        return frame
    renamed = frame.rename(
        columns={
            "trade_date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "pct_chg": "涨跌幅",
            "vol": "成交量",
            "amount": "成交额",
        }
    ).copy()
    if "日期" in renamed.columns:
        renamed["日期"] = renamed["日期"].astype(str).str.replace(
            r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True
        )
        renamed = renamed.sort_values("日期")
    return renamed


def fetch_tushare_daily(stock_code, end_date, lookback_days=MARKET_LOOKBACK_DAYS):
    if not TUSHARE_TOKEN:
        return {"ok": False, "error": "Tushare 未配置：请在 .env 设置 TUSHARE_TOKEN"}
    try:
        import tushare as ts
    except ImportError:
        return {
            "ok": False,
            "error": "Tushare 未安装。请运行：python3 -m pip install tushare",
        }

    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=lookback_days * 2)
    try:
        pro = ts.pro_api(TUSHARE_TOKEN)
        frame = pro.daily(
            ts_code=tushare_ts_code(stock_code),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception as exc:
        return {"ok": False, "error": f"Tushare 拉取失败：{exc}"}
    if frame is None or frame.empty:
        return {"ok": False, "error": f"Tushare 未返回 {stock_code} 的日 K 数据"}
    return {"ok": True, "frame": normalize_tushare_frame(frame), "provider": "tushare"}


def fetch_akshare_daily(stock_code, end_date, lookback_days=MARKET_LOOKBACK_DAYS):
    try:
        import akshare as ak
    except ImportError:
        return {
            "ok": False,
            "error": "AKShare 未安装。请运行：python3 -m pip install akshare",
        }

    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=lookback_days * 2)
    symbol = compact_stock_code(stock_code)
    errors = []
    for attempt in range(3):
        try:
            frame = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
            break
        except Exception as exc:
            errors.append(f"eastmoney attempt {attempt + 1}: {exc}")
            time.sleep(0.8 * (attempt + 1))
    else:
        frame = None

    if frame is None or frame.empty:
        try:
            tx_frame = ak.stock_zh_a_hist_tx(
                symbol=market_prefixed_code(symbol),
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
                timeout=20,
            )
            frame = normalize_tx_frame(tx_frame)
        except Exception as exc:
            errors.append(f"tencent fallback: {exc}")
            return {"ok": False, "error": "AKShare 拉取失败：" + " | ".join(errors)}
    if frame is None or frame.empty:
        return {"ok": False, "error": f"AKShare 未返回 {stock_code} 的日 K 数据"}
    return {"ok": True, "frame": frame, "provider": "akshare"}


def fetch_market_daily(stock_code, end_date):
    provider = MARKET_DATA_PROVIDER
    if provider == "akshare":
        result = fetch_akshare_daily(stock_code, end_date)
        if not result.get("ok"):
            result["stock_code"] = stock_code
        return result
    if provider == "tushare":
        result = fetch_tushare_daily(stock_code, end_date)
        if result.get("ok"):
            return result
        fallback = fetch_akshare_daily(stock_code, end_date)
        if fallback.get("ok"):
            fallback["provider"] = "akshare_fallback"
            fallback["fallback_reason"] = result.get("error")
            return fallback
        return {
            "ok": False,
            "stock_code": stock_code,
            "error": f"{result.get('error')}；AKShare fallback 也失败：{fallback.get('error')}",
        }
    if provider in ("auto", "tushare_akshare"):
        result = fetch_tushare_daily(stock_code, end_date)
        if result.get("ok"):
            return result
        fallback = fetch_akshare_daily(stock_code, end_date)
        if fallback.get("ok"):
            fallback["provider"] = "akshare_fallback"
            fallback["fallback_reason"] = result.get("error")
            return fallback
        return {
            "ok": False,
            "stock_code": stock_code,
            "error": f"{result.get('error')}；AKShare fallback 也失败：{fallback.get('error')}",
        }
    return {"ok": False, "stock_code": stock_code, "error": f"Unsupported market provider: {provider}"}


def frame_records(frame):
    records = []
    for row in frame.to_dict(orient="records"):
        item = {}
        for key, value in row.items():
            if hasattr(value, "item"):
                value = value.item()
            item[str(key)] = value
        records.append(item)
    return records


def mean(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 4) if values else None


def market_context_for_stock(stock_code, trade_date, trades=None):
    daily = fetch_market_daily(stock_code, trade_date)
    if not daily.get("ok"):
        return daily
    rows = frame_records(daily["frame"])
    rows = [row for row in rows if str(row.get("日期", "")) <= trade_date]
    if not rows:
        return {"ok": False, "error": f"{stock_code} 在 {trade_date} 前无 K 线数据"}
    recent = rows[-60:]
    latest = recent[-1]
    closes = [as_number(row.get("收盘")) for row in recent]
    volumes = [as_number(row.get("成交量")) for row in recent]
    high = as_number(latest.get("最高"))
    low = as_number(latest.get("最低"))
    close = as_number(latest.get("收盘"))
    open_price = as_number(latest.get("开盘"))
    volume = as_number(latest.get("成交量"))
    volume_20_avg = mean(volumes[-20:])

    trade_points = []
    for trade in trades or []:
        price = as_number(trade.get("price"))
        position = None
        if price is not None and high is not None and low is not None and high != low:
            position = round((price - low) / (high - low), 4)
        trade_points.append(
            {
                "side": trade.get("side"),
                "price": price,
                "quantity": trade.get("quantity"),
                "amount": trade.get("amount"),
                "position_in_day_range": position,
            }
        )

    return {
        "ok": True,
        "provider": daily.get("provider") or MARKET_DATA_PROVIDER,
        "fallback_reason": daily.get("fallback_reason"),
        "stock_code": stock_code,
        "k_date": str(latest.get("日期")),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "pct_change": as_number(latest.get("涨跌幅")),
        "volume": volume,
        "amount": as_number(latest.get("成交额")),
        "turnover_rate": as_number(latest.get("换手率")),
        "ma5": mean(closes[-5:]),
        "ma10": mean(closes[-10:]),
        "ma20": mean(closes[-20:]),
        "volume_vs_20d_avg": round(volume / volume_20_avg, 4)
        if volume is not None and volume_20_avg
        else None,
        "trade_points": trade_points,
    }


def market_context_for_trades(trades, trade_date):
    contexts = []
    grouped = {}
    for trade in trades:
        code = trade.get("stock_code")
        if not code:
            continue
        grouped.setdefault(code, []).append(trade)
    for code, items in grouped.items():
        contexts.append(market_context_for_stock(code, trade_date, items))
    return contexts


def compact_watch_item(item):
    return {
        "code": item.get("stock_code"),
        "name": item.get("stock_name"),
        "sector": item.get("sector_name"),
        "rank": item.get("sector_rank"),
        "strategy": item.get("strategy_type"),
        "support": item.get("support_price"),
        "resistance": item.get("resistance_price"),
        "stop_loss": item.get("stop_loss"),
        "reason": item.get("reason"),
    }


def compact_market_context(context):
    if not isinstance(context, dict):
        return context
    if not context.get("ok"):
        return {
            "ok": False,
            "stock_code": context.get("stock_code"),
            "error": context.get("error"),
        }
    return {
        "ok": True,
        "code": context.get("stock_code"),
        "date": context.get("k_date"),
        "open": context.get("open"),
        "high": context.get("high"),
        "low": context.get("low"),
        "close": context.get("close"),
        "pct": context.get("pct_change"),
        "ma5": context.get("ma5"),
        "ma10": context.get("ma10"),
        "ma20": context.get("ma20"),
        "vol20": context.get("volume_vs_20d_avg"),
    }


def upsert_watch(payload):
    item = {
        "id": str(uuid.uuid4()),
        "stock_code": str(payload.get("stock_code") or "").strip(),
        "stock_name": str(payload.get("stock_name") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "sector_name": str(payload.get("sector_name") or "").strip(),
        "sector_rank": as_int(payload.get("sector_rank")),
        "strategy_type": str(payload.get("strategy_type") or "").strip(),
        "support_price": payload.get("support_price") or None,
        "resistance_price": payload.get("resistance_price") or None,
        "target_buy_min": payload.get("target_buy_min") or None,
        "target_buy_max": payload.get("target_buy_max") or None,
        "stop_loss": payload.get("stop_loss") or None,
        "max_position": str(payload.get("max_position") or "").strip(),
        "active": 1,
        "created_at": now_iso(),
    }
    if not item["stock_code"]:
        raise ValueError("stock_code is required")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO watchlist
            (id, stock_code, stock_name, reason, sector_name, sector_rank,
             strategy_type, support_price,
             resistance_price, target_buy_min, target_buy_max, stop_loss,
             max_position, active, created_at)
            VALUES
            (:id, :stock_code, :stock_name, :reason, :sector_name, :sector_rank,
             :strategy_type, :support_price,
             :resistance_price, :target_buy_min, :target_buy_max, :stop_loss,
             :max_position, :active, :created_at)
            ON CONFLICT(stock_code) DO UPDATE SET
              stock_name=excluded.stock_name,
              reason=excluded.reason,
              sector_name=COALESCE(NULLIF(excluded.sector_name, ''), watchlist.sector_name),
              sector_rank=COALESCE(excluded.sector_rank, watchlist.sector_rank),
              strategy_type=excluded.strategy_type,
              support_price=excluded.support_price,
              resistance_price=excluded.resistance_price,
              target_buy_min=excluded.target_buy_min,
              target_buy_max=excluded.target_buy_max,
              stop_loss=excluded.stop_loss,
              max_position=excluded.max_position,
              active=1
            """,
            item,
        )
    return item


def generate_trade_review(trade_date):
    trades = list_trades(trade_date)
    market_context = market_context_for_trades(trades, trade_date)
    prompt = (
        "你是一个股票交易复盘助手，只分析交易纪律和风险控制，不提供荐股。\n"
        "请根据交易记录和本地计算的 K 线摘要输出：今日总结、可能的问题、纪律风险、明日改进清单、标签。\n"
        "如果行情数据缺失，请明确说明不要猜测走势。\n"
        "请保持具体、克制，不要承诺收益。\n"
        f"交易日期：{trade_date}\n"
        f"交易记录：{json.dumps(trades, ensure_ascii=False)}\n"
        f"K线摘要：{json.dumps(market_context, ensure_ascii=False)}"
    )
    result = ai_complete([prompt])
    with db() as conn:
        conn.execute(
            """
            INSERT INTO trade_reviews
            (id, trade_date, summary, mistakes, lessons, ai_review, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
              ai_review=excluded.ai_review,
              created_at=excluded.created_at
            """,
            (
                str(uuid.uuid4()),
                trade_date,
                "",
                "",
                "",
                result["text"],
                now_iso(),
            ),
        )
    return result["text"]


def generate_watch_report():
    watch_items = list_watchlist()
    market_context = [
        market_context_for_stock(item["stock_code"], today_str())
        for item in watch_items
        if item.get("stock_code")
    ]
    compact_items = [compact_watch_item(item) for item in watch_items]
    compact_context = [compact_market_context(item) for item in market_context]
    prompt = (
        "你是一个自选股盘后观察助手。不要给直接买入建议，不要预测确定收益。\n"
        "基于用户预设条件和本地计算的 K 线摘要，按三类输出：重点观察、继续跟踪、暂时回避。\n"
        "如果某只股票行情数据缺失，请明确说明，不要猜测走势。\n"
        "请精简输出，每只股票最多 3 条要点，总字数控制在 1200 字以内。\n"
        f"日期：{today_str()}\n"
        f"自选股：{json.dumps(compact_items, ensure_ascii=False)}\n"
        f"K线摘要：{json.dumps(compact_context, ensure_ascii=False)}"
    )
    result = ai_complete([prompt])
    report_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """
            INSERT INTO daily_stock_reports
            (id, report_date, market_data_json, ai_summary, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (report_id, today_str(), "{}", result["text"], now_iso()),
        )
    return result["text"]


def sector_watch_groups():
    groups = {}
    for item in list_watchlist():
        sector = item.get("sector_name") or "未分组"
        groups.setdefault(sector, []).append(item)
    for items in groups.values():
        items.sort(key=lambda item: item.get("sector_rank") or 999)
    return groups


def sector_market_snapshot(trade_date=None):
    trade_date = trade_date or today_str()
    sectors = []
    for sector, items in sector_watch_groups().items():
        if sector == "未分组":
            continue
        stocks = []
        for item in items[:6]:
            code = item.get("stock_code")
            if not code:
                continue
            context = market_context_for_stock(code, trade_date)
            compact = compact_market_context(context)
            compact.update(
                {
                    "name": item.get("stock_name"),
                    "sector_rank": item.get("sector_rank"),
                }
            )
            stocks.append(compact)
        ok_stocks = [stock for stock in stocks if stock.get("ok")]
        if not ok_stocks:
            sectors.append({"sector": sector, "stocks": stocks, "ok": False})
            continue
        pct_values = [stock.get("pct") for stock in ok_stocks if stock.get("pct") is not None]
        above_ma5 = sum(
            1
            for stock in ok_stocks
            if stock.get("close") is not None
            and stock.get("ma5") is not None
            and stock["close"] >= stock["ma5"]
        )
        above_ma20 = sum(
            1
            for stock in ok_stocks
            if stock.get("close") is not None
            and stock.get("ma20") is not None
            and stock["close"] >= stock["ma20"]
        )
        near_ma5 = sum(
            1
            for stock in ok_stocks
            if stock.get("close") is not None
            and stock.get("ma5") is not None
            and abs(stock["close"] - stock["ma5"]) / stock["ma5"] <= 0.03
        )
        sectors.append(
            {
                "sector": sector,
                "ok": True,
                "stock_count": len(ok_stocks),
                "avg_pct": round(sum(pct_values) / len(pct_values), 3) if pct_values else None,
                "above_ma5": above_ma5,
                "above_ma20": above_ma20,
                "near_ma5": near_ma5,
                "stocks": stocks,
            }
        )
    sectors.sort(
        key=lambda item: (
            item.get("above_ma5", 0),
            item.get("near_ma5", 0),
            item.get("avg_pct") or -999,
        ),
        reverse=True,
    )
    return sectors


def generate_sector_rotation_report(trade_date=None):
    trade_date = trade_date or today_str()
    snapshot = sector_market_snapshot(trade_date)
    prompt = (
        "你是 A 股 AI 产业链赛道轮动观察助手。不要给确定买入建议，不要承诺收益。\n"
        "用户关注的是：AI 各细分赛道此消彼长，寻找持续走低后开始企稳、适合后续低吸观察的赛道。\n"
        "请基于本地 K 线摘要，按以下结构输出：\n"
        "1. 低位企稳观察赛道：重点找前期弱、但出现站回 MA5/跌幅收窄/量能改善/多只核心股同步修复的赛道。\n"
        "2. 强势延续但不追赛道：说明强在哪里，以及等什么回踩信号。\n"
        "3. 仍在走弱赛道：说明为什么暂时不急。\n"
        "4. 明日观察触发条件：用可执行条件表达，例如“赛道内 top4 至少 2 只站上 MA5”。\n"
        "请优先按赛道分析，再点名赛道内 top1-top4 核心股票。总字数控制在 1400 字以内。\n"
        f"日期：{trade_date}\n"
        f"赛道快照：{json.dumps(snapshot, ensure_ascii=False)}"
    )
    result = ai_complete([prompt])
    with db() as conn:
        conn.execute(
            """
            INSERT INTO daily_stock_reports
            (id, report_date, market_data_json, ai_summary, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                trade_date,
                json.dumps({"type": "sector_rotation", "snapshot": snapshot}, ensure_ascii=False),
                result["text"],
                now_iso(),
            ),
        )
    return result["text"]


def normalize_image_type(image_type):
    value = (image_type or "auto").strip().lower()
    aliases = {
        "sb": "intraday_sb",
        "s/b": "intraday_sb",
        "intraday": "intraday_sb",
        "intraday_sb": "intraday_sb",
        "daytrade": "intraday_sb",
        "日内": "intraday_sb",
        "分时": "intraday_sb",
        "k线": "intraday_sb",
        "kline": "intraday_sb",
        "broker": "broker_records",
        "records": "broker_records",
        "trades": "broker_records",
        "成交": "broker_records",
        "券商": "broker_records",
        "交割": "broker_records",
        "watchlist": "watchlist_snapshot",
        "watch": "watchlist_snapshot",
        "自选": "watchlist_snapshot",
        "自选股": "watchlist_snapshot",
        "优选": "watchlist_snapshot",
        "auto": "auto",
        "自动": "auto",
    }
    return aliases.get(value, "auto")


def screenshot_prompt(image_type):
    common = (
        "你是股票交易截图结构化识别助手。只识别图片中能看清的信息，不要猜测、不要补全。\n"
        "输出必须是 JSON，不要 Markdown，不要解释。\n"
        "如果某个字段看不清，填 null；如果完全无法识别，返回 {\"type\":\"unknown\",\"reason\":\"...\"}。\n"
    )
    if image_type == "intraday_sb":
        return (
            common
            + "这类图片是日内交易/分时/K线图，上面可能有 S 或 B 标记。\n"
            "请识别每个 S/B 点对应的交易信息。重点看标记附近的价格、时间、成交方向；金额如果图片没有直接显示，"
            "不要编造，填 null。\n"
            "返回结构：{\"type\":\"intraday_sb\",\"trades\":[{\"marker\":\"B或S\",\"side\":\"buy或sell\","
            "\"time\":null,\"price\":null,\"quantity\":null,\"amount\":null,\"stock_code\":null,\"stock_name\":null,"
            "\"confidence\":0到1,\"evidence\":\"你看到的关键文字或位置\"}],\"warnings\":[]}"
        )
    if image_type == "broker_records":
        return (
            common
            + "这类图片通常是券商软件的“当日委托/当日成交/历史委托/历史成交”列表。\n"
            "如果表头类似“委托时间、委托/均价、委托/成交、状态”，每条记录通常有两行数字："
            "第一行是委托价和委托数量，第二行是成交均价和成交数量。\n"
            "请逐行提取所有委托记录，并严格区分已成交和未成交：状态为“已成”且成交数量大于 0 才算真实成交；"
            "状态为“已报”、成交数量为 0、均价为 0.000 的记录不要放入 filled_trades。\n"
            "买卖方向可从颜色、文字“买/卖”、右侧状态列中的“买入/卖出”判断；红色通常是买入，蓝色通常是卖出。\n"
            "金额用成交均价 * 成交数量计算，字段 computed_amount=true；如果图片直接有成交金额则优先读取并标注 computed_amount=false。\n"
            "返回结构：{\"type\":\"broker_records\",\"orders\":[{\"trade_date\":null,\"time\":null,"
            "\"stock_code\":null,\"stock_name\":null,\"side\":\"buy或sell\",\"order_price\":null,\"avg_price\":null,"
            "\"order_quantity\":null,\"filled_quantity\":null,\"amount\":null,\"computed_amount\":true,"
            "\"status\":null,\"is_filled\":false,\"confidence\":0到1,\"raw_text\":\"该行原文\"}],"
            "\"filled_trades\":[{\"trade_date\":null,\"time\":null,\"stock_code\":null,\"stock_name\":null,"
            "\"side\":\"buy或sell\",\"price\":null,\"quantity\":null,\"amount\":null,\"source_order_index\":null}],"
            "\"warnings\":[]}"
        )
    if image_type == "watchlist_snapshot":
        return (
            common
            + "这类图片是股票 App 的自选股/优选列表截图，通常每行包含股票名称、股票代码、涨跌幅、现价和小型走势图。\n"
            "请逐行提取屏幕中可见的股票，不要提取顶部栏目名称。代码通常是 6 位数字，股票名称在代码上一行或左侧。\n"
            "涨跌幅可能带 + 或 - 和 %，现价在涨跌幅下面。不要把走势图形状当作价格数据。\n"
            "返回结构：{\"type\":\"watchlist_snapshot\",\"items\":[{\"stock_code\":null,\"stock_name\":null,"
            "\"pct_change\":null,\"last_price\":null,\"board\":null,\"rank\":null,\"confidence\":0到1}],\"warnings\":[]}"
        )
    return (
        common
        + "请先判断截图类型：intraday_sb（日内图上 S/B 点）、broker_records（券商成交记录列表）"
        "或 watchlist_snapshot（自选股列表）。\n"
        "如果是日内 S/B 点，按 intraday_sb 结构输出；如果是券商成交记录合集，按 broker_records 结构输出；"
        "如果是自选股列表，按 watchlist_snapshot 结构输出。"
    )


def infer_telegram_image_type(message):
    caption = (message.get("caption") or "").strip().lower()
    if any(token in caption for token in ("日内", "分时", "sb", "s/b", "k线", "kline", "intraday")):
        return "intraday_sb"
    if any(token in caption for token in ("成交", "券商", "交割", "记录", "broker", "records")):
        return "broker_records"
    if any(token in caption for token in ("自选", "优选", "watchlist", "watch")):
        return "watchlist_snapshot"
    return "auto"


def parse_ai_json(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def normalize_side(value):
    text = str(value or "").strip().lower()
    if text in ("buy", "买", "买入", "b"):
        return "buy"
    if text in ("sell", "卖", "卖出", "s"):
        return "sell"
    if "买" in text:
        return "buy"
    if "卖" in text:
        return "sell"
    return None


def as_number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value):
    number = as_number(value)
    return int(number) if number is not None else None


def cached_stock_code(stock_name):
    name = str(stock_name or "").strip()
    if not name:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT stock_code FROM stock_code_cache WHERE stock_name = ?", (name,)
        ).fetchone()
    return row["stock_code"] if row else None


def save_stock_code_cache(stock_name, stock_code, source):
    name = str(stock_name or "").strip()
    code = str(stock_code or "").strip()
    if not name or not code:
        return
    with db() as conn:
        conn.execute(
            """
            INSERT INTO stock_code_cache (stock_name, stock_code, source, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_name) DO UPDATE SET
              stock_code=excluded.stock_code,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (name, code, source, now_iso()),
        )


def lookup_stock_code(stock_name, price=None):
    name = str(stock_name or "").strip()
    if not name:
        return None
    cached = cached_stock_code(name)
    if cached:
        return cached
    try:
        import akshare as ak
    except ImportError:
        return None

    try:
        stocks = ak.stock_info_a_code_name()
        exact = stocks[stocks["name"] == name]
        if not exact.empty:
            code = str(exact.iloc[0]["code"])
            save_stock_code_cache(name, code, "stock_info_a_code_name")
            return code
    except Exception:
        pass

    try:
        etfs = ak.fund_etf_spot_em()
        candidates = etfs[etfs["名称"].astype(str).str.contains(name, regex=False)]
        if not candidates.empty:
            selected = candidates.iloc[0]
            trade_price = as_number(price)
            if trade_price is not None and "最新价" in candidates.columns:
                candidates = candidates.copy()
                candidates["_price_diff"] = candidates["最新价"].apply(
                    lambda value: abs((as_number(value) or 0) - trade_price)
                )
                selected = candidates.sort_values("_price_diff").iloc[0]
            code = str(selected["代码"])
            save_stock_code_cache(name, code, "fund_etf_spot_em")
            return code
    except Exception:
        pass
    return None


def trade_date_from_time(value):
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return today_str()


def trade_date_for_screenshot_trade(trade, screenshot):
    if trade.get("trade_date"):
        return trade.get("trade_date")
    text = str(trade.get("time") or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return trade_date_from_time(text)
    created_at = str((screenshot or {}).get("created_at") or "")
    if len(created_at) >= 10:
        return created_at[:10]
    return today_str()


def normalize_broker_result(text):
    parsed = parse_ai_json(text)
    if not isinstance(parsed, dict):
        return text
    if parsed.get("type") != "broker_records":
        return text

    raw_orders = parsed.get("orders")
    if raw_orders is None:
        raw_orders = parsed.get("records") or parsed.get("data") or parsed.get("trades") or []
    if not isinstance(raw_orders, list):
        return text

    orders = []
    filled_trades = []
    for index, raw in enumerate(raw_orders):
        if not isinstance(raw, dict):
            continue
        avg_price = as_number(raw.get("avg_price") or raw.get("price"))
        if avg_price is None:
            avg_price = as_number(raw.get("deal_price") or raw.get("成交均价"))
        filled_quantity = as_int(
            raw.get("filled_quantity")
            if raw.get("filled_quantity") is not None
            else raw.get("executed_qty")
            if raw.get("executed_qty") is not None
            else raw.get("executed_quantity")
            if raw.get("executed_quantity") is not None
            else raw.get("deal_volume")
        )
        order_price = as_number(raw.get("order_price") or raw.get("委托价"))
        order_quantity = as_int(
            raw.get("order_quantity")
            if raw.get("order_quantity") is not None
            else raw.get("order_qty")
            if raw.get("order_qty") is not None
            else raw.get("order_volume")
        )
        status = raw.get("status")
        status_text = str(status or "").strip()
        raw_is_filled = raw.get("is_filled")
        is_filled = (
            bool(raw_is_filled)
            or ("已成" in status_text and (filled_quantity or 0) > 0)
            or (
                (filled_quantity or 0) > 0
                and avg_price is not None
                and avg_price > 0
                and "已报" not in status_text
            )
        )
        amount = as_number(raw.get("amount"))
        computed_amount = False
        if amount is None and is_filled and avg_price is not None and filled_quantity is not None:
            amount = round(avg_price * filled_quantity, 3)
            computed_amount = True

        order = {
            "trade_date": raw.get("trade_date"),
            "time": raw.get("time"),
            "stock_code": raw.get("stock_code"),
            "stock_name": raw.get("stock_name") or raw.get("name"),
            "side": normalize_side(raw.get("side") or raw.get("direction") or status),
            "order_price": order_price,
            "avg_price": avg_price,
            "order_quantity": order_quantity,
            "filled_quantity": filled_quantity,
            "amount": amount,
            "computed_amount": computed_amount,
            "status": status,
            "is_filled": is_filled,
            "confidence": raw.get("confidence"),
            "raw_text": raw.get("raw_text"),
        }
        orders.append(order)
        if is_filled:
            filled_trades.append(
                {
                    "trade_date": order["trade_date"],
                    "time": order["time"],
                    "stock_code": order["stock_code"],
                    "stock_name": order["stock_name"],
                    "side": order["side"],
                    "price": avg_price,
                    "quantity": filled_quantity,
                    "amount": amount,
                    "source_order_index": index,
                }
            )

    normalized = {
        "type": "broker_records",
        "orders": orders,
        "filled_trades": filled_trades,
        "warnings": parsed.get("warnings") or [],
    }
    return json.dumps(normalized, ensure_ascii=False, indent=2)


def enrich_broker_result_codes(text):
    parsed = parse_ai_json(text)
    if not isinstance(parsed, dict) or parsed.get("type") != "broker_records":
        return text
    for order in parsed.get("orders") or []:
        if isinstance(order, dict) and not order.get("stock_code"):
            order["stock_code"] = lookup_stock_code(
                order.get("stock_name"), order.get("avg_price") or order.get("order_price")
            )
    for trade in parsed.get("filled_trades") or []:
        if isinstance(trade, dict) and not trade.get("stock_code"):
            trade["stock_code"] = lookup_stock_code(trade.get("stock_name"), trade.get("price"))
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def normalize_watchlist_result(text):
    parsed = parse_ai_json(text)
    if not isinstance(parsed, dict) or parsed.get("type") != "watchlist_snapshot":
        return text
    raw_items = parsed.get("items")
    if raw_items is None:
        raw_items = parsed.get("data") or parsed.get("stocks") or []
    if not isinstance(raw_items, list):
        return text

    items = []
    defer_code_lookup = parsed.get("recognition_source") == "macos_vision_ocr"
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        stock_code = str(
            raw.get("stock_code")
            or raw.get("code")
            or raw.get("代码")
            or ""
        ).strip()
        stock_name = str(
            raw.get("stock_name")
            or raw.get("name")
            or raw.get("名称")
            or ""
        ).strip()
        pct_change = raw.get("pct_change")
        if pct_change is None:
            pct_change = raw.get("change_percent") or raw.get("涨跌幅")
        last_price = raw.get("last_price")
        if last_price is None:
            last_price = raw.get("price") or raw.get("现价") or raw.get("最新价")
        if not stock_code and stock_name and not defer_code_lookup:
            stock_code = lookup_stock_code(stock_name, last_price) or ""
        items.append(
            {
                "stock_code": stock_code or None,
                "stock_name": stock_name or None,
                "pct_change": pct_change,
                "last_price": as_number(last_price),
                "board": raw.get("board") or raw.get("板块"),
                "rank": raw.get("rank") or raw.get("排名") or index + 1,
                "confidence": raw.get("confidence"),
            }
        )
    normalized = {
        "type": "watchlist_snapshot",
        "recognition_source": parsed.get("recognition_source"),
        "items": items,
        "warnings": parsed.get("warnings") or [],
    }
    return json.dumps(normalized, ensure_ascii=False, indent=2)


def has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def clean_ocr_text(text):
    return str(text or "").replace(" ", "").strip()


def clean_ocr_stock_name(text):
    value = clean_ocr_text(text)
    replacements = {
        "Al芯片": "AI芯片",
        "由福晶科技": "福晶科技",
        "浪信息": "浪潮信息",
        "浪›信息": "浪潮信息",
        "沐㬢股份": "沐曦股份",
        "沐賺股份": "沐曦股份",
        "亭通光电": "亨通光电",
        "三南网数字": "南网数字",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    first_cjk = None
    for index, char in enumerate(value):
        if "\u4e00" <= char <= "\u9fff":
            first_cjk = index
            break
    if first_cjk is not None and not value.startswith(("AI", "AIDC")):
        value = value[first_cjk:]
    return value


def run_local_ocr(image_path):
    if not LOCAL_OCR_ENABLED:
        return {"ok": False, "error": "LOCAL_OCR_ENABLED=false"}
    script = ROOT / "scripts" / "vision_ocr.swift"
    if not script.exists():
        return {"ok": False, "error": "OCR script not found"}
    cache_dir = DATA_DIR / "swift-module-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(cache_dir)
    try:
        completed = subprocess.run(
            ["swift", str(script), str(image_path)],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            timeout=LOCAL_OCR_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Swift not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "OCR timed out"}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {"ok": False, "error": detail or f"OCR exited {completed.returncode}"}
    try:
        observations = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"OCR JSON parse failed: {exc}"}
    return {"ok": True, "observations": observations}


def parse_pct_text(text):
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", str(text or ""))
    return float(match.group(1)) if match else None


def parse_price_text(text):
    numbers = re.findall(r"\d+(?:\.\d+)?", str(text or ""))
    return float(numbers[-1]) if numbers else None


def ocr_rows_by_y(observations, row_step=0.048):
    rows = {}
    for item in observations:
        text = clean_ocr_text(item.get("text"))
        if not text:
            continue
        y = as_number(item.get("y"))
        if y is None:
            continue
        key = round(y / row_step) * row_step
        rows.setdefault(key, []).append(item)
    for values in rows.values():
        values.sort(key=lambda item: as_number(item.get("x")) or 0)
    return rows


def parse_ocr_board_watchlist(observations):
    texts = [clean_ocr_text(item.get("text")) for item in observations]
    header_hits = sum(1 for text in texts if text.lower() in ("top1", "topl", "top2", "top3", "top4"))
    if "分类" not in texts or header_hits < 2:
        return None

    columns = [
        ("board", 0.00, 0.15),
        ("top1", 0.15, 0.36),
        ("top2", 0.36, 0.58),
        ("top3", 0.58, 0.80),
        ("top4", 0.80, 1.01),
    ]
    headers = {"分类", "top1", "topl", "top2", "top3", "top4"}
    rows = {}
    for item in observations:
        text = clean_ocr_text(item.get("text"))
        if not text or text in headers:
            continue
        x = as_number(item.get("x"))
        y = as_number(item.get("y"))
        if x is None or y is None:
            continue
        column = next((name for name, left, right in columns if left <= x < right), None)
        if not column:
            continue
        if column != "board" and not has_cjk(text):
            continue
        if column == "board" and not (has_cjk(text) or text in ("CPO", "OCS", "PCB", "AIDC")):
            continue
        key = round(y / 0.048) * 0.048
        rows.setdefault(key, {}).setdefault(column, []).append(text)

    items = []
    for _, row in sorted(rows.items(), reverse=True):
        if "board" not in row:
            continue
        board = clean_ocr_stock_name("".join(row.get("board") or []))
        for rank, column in enumerate(("top1", "top2", "top3", "top4"), start=1):
            values = row.get(column) or []
            stock_name = clean_ocr_stock_name("".join(values))
            if not stock_name or not has_cjk(stock_name):
                continue
            items.append(
                {
                    "stock_code": None,
                    "stock_name": stock_name,
                    "pct_change": None,
                    "last_price": None,
                    "board": board,
                    "rank": rank,
                    "confidence": 0.82,
                    "source": "macos_vision_ocr",
                }
            )
    if len(items) < 4:
        return None
    return {
        "type": "watchlist_snapshot",
        "recognition_source": "macos_vision_ocr",
        "items": items,
        "warnings": ["OCR 快速识别结果，请在导入前确认股票名。"],
    }


def parse_ocr_stock_list(observations):
    rows = ocr_rows_by_y(observations, row_step=0.036)
    items = []
    for _, values in sorted(rows.items(), reverse=True):
        joined = " ".join(clean_ocr_text(item.get("text")) for item in values)
        code_match = re.search(r"\b([0368]\d{5})\b", joined)
        if not code_match:
            continue
        code = code_match.group(1)
        name_candidates = [
            clean_ocr_stock_name(item.get("text"))
            for item in values
            if has_cjk(item.get("text")) and not re.search(r"\d{6}", str(item.get("text")))
        ]
        if not name_candidates:
            continue
        stock_name = max(name_candidates, key=len)
        pct = parse_pct_text(joined)
        price = None
        numeric_right = [
            item
            for item in values
            if (as_number(item.get("x")) or 0) > 0.72
            and re.search(r"\d+(?:\.\d+)?", str(item.get("text") or ""))
        ]
        if numeric_right:
            price = parse_price_text(" ".join(str(item.get("text")) for item in numeric_right))
        items.append(
            {
                "stock_code": code,
                "stock_name": stock_name,
                "pct_change": pct,
                "last_price": price,
                "board": None,
                "rank": len(items) + 1,
                "confidence": 0.86,
                "source": "macos_vision_ocr",
            }
        )
    if len(items) < 2:
        return None
    return {
        "type": "watchlist_snapshot",
        "recognition_source": "macos_vision_ocr",
        "items": items,
        "warnings": ["OCR 快速识别结果，请在导入前确认股票代码和价格。"],
    }


def parse_ocr_broker_records(observations):
    texts = [clean_ocr_text(item.get("text")) for item in observations]
    deal_header_hits = sum(
        1 for text in texts if any(token in text for token in ("成交时间", "成交价", "成交量", "成交额"))
    )
    if deal_header_hits >= 3:
        deal_result = parse_ocr_broker_filled_records(observations)
        if deal_result:
            return deal_result

    header_hits = sum(
        1
        for text in texts
        if text in ("委托时间", "委托/均价", "委托/成交", "状态", "当日委托", "历史委托")
    )
    if header_hits < 3:
        return None

    stock_rows = []
    for item in observations:
        text = clean_ocr_stock_name(item.get("text"))
        x = as_number(item.get("x")) or 0
        y = as_number(item.get("y"))
        if y is None or not (0.0 <= x <= 0.25) or not has_cjk(text):
            continue
        if any(token in text for token in ("委托", "成交", "状态", "自定义", "近")):
            continue
        if text in ("买", "卖", "买入", "卖出", "成", "已成", "已报"):
            continue
        if re.search(r"\d{6,}", text):
            continue
        stock_rows.append({"stock_name": text, "y": y})
    stock_rows.sort(key=lambda item: item["y"], reverse=True)
    if not stock_rows:
        return None

    numeric = []
    statuses = []
    details = []
    for item in observations:
        text = clean_ocr_text(item.get("text"))
        x = as_number(item.get("x")) or 0
        y = as_number(item.get("y"))
        if y is None:
            continue
        if 0.34 <= x <= 0.56 and re.search(r"\d+(?:\.\d+)?", text):
            numeric.append({"kind": "price", "value": as_number(text), "y": y, "text": text})
        elif 0.56 <= x <= 0.76 and re.search(r"\d+", text):
            numeric.append({"kind": "qty", "value": as_int(text), "y": y, "text": text})
        elif x >= 0.82 and has_cjk(text):
            statuses.append({"text": text, "y": y})
        elif x <= 0.32 and re.search(r"\d{8}\s*\d{1,2}:\d{2}:\d{2}", text):
            details.append({"text": text, "y": y})

    orders = []
    filled_trades = []
    for index, row in enumerate(stock_rows):
        y_top = row["y"]
        y_next = stock_rows[index + 1]["y"] if index + 1 < len(stock_rows) else -1
        y_low = max(y_next + 0.012, y_top - 0.105)

        band_numbers = [
            item for item in numeric if y_low <= item["y"] <= y_top + 0.035
        ]
        prices = sorted(
            [item for item in band_numbers if item["kind"] == "price"],
            key=lambda item: item["y"],
            reverse=True,
        )
        qtys = sorted(
            [item for item in band_numbers if item["kind"] == "qty"],
            key=lambda item: item["y"],
            reverse=True,
        )
        row_statuses = [
            item["text"] for item in statuses if y_low <= item["y"] <= y_top + 0.035
        ]
        row_details = [
            item["text"] for item in details if y_low <= item["y"] <= y_top + 0.035
        ]
        detail_text = " ".join(row_details)
        status_text = " ".join(row_statuses)
        side = normalize_side(detail_text or status_text)
        order_price = prices[0]["value"] if prices else None
        avg_price = prices[1]["value"] if len(prices) > 1 else None
        order_quantity = qtys[0]["value"] if qtys else None
        filled_quantity = qtys[1]["value"] if len(qtys) > 1 else None
        is_filled = (
            (filled_quantity or 0) > 0
            and avg_price is not None
            and avg_price > 0
            and ("已报" not in status_text)
        )
        amount = (
            round(avg_price * filled_quantity, 3)
            if is_filled and avg_price is not None and filled_quantity is not None
            else None
        )
        time_text = detail_text
        order = {
            "trade_date": trade_date_from_time(time_text),
            "time": time_text or None,
            "stock_code": None,
            "stock_name": row["stock_name"],
            "side": side,
            "order_price": order_price,
            "avg_price": avg_price,
            "order_quantity": order_quantity,
            "filled_quantity": filled_quantity,
            "amount": amount,
            "computed_amount": amount is not None,
            "status": status_text or None,
            "is_filled": is_filled,
            "confidence": 0.82,
            "raw_text": " ".join(
                [row["stock_name"], detail_text, status_text]
                + [item["text"] for item in band_numbers]
            ).strip(),
        }
        orders.append(order)
        if is_filled:
            filled_trades.append(
                {
                    "trade_date": order["trade_date"],
                    "time": order["time"],
                    "stock_code": None,
                    "stock_name": order["stock_name"],
                    "side": side,
                    "price": avg_price,
                    "quantity": filled_quantity,
                    "amount": amount,
                    "source_order_index": index,
                }
            )

    if not orders:
        return None
    return {
        "type": "broker_records",
        "recognition_source": "macos_vision_ocr",
        "orders": orders,
        "filled_trades": filled_trades,
        "warnings": ["OCR 快速识别结果，请在导入前确认成交状态、价格和数量。"],
    }


def parse_ocr_broker_filled_records(observations):
    stock_rows = []
    for item in observations:
        text = clean_ocr_stock_name(item.get("text"))
        x = as_number(item.get("x")) or 0
        y = as_number(item.get("y"))
        if y is None or not (0.0 <= x <= 0.25) or not has_cjk(text):
            continue
        if any(token in text for token in ("委托", "成交", "状态", "自定义", "默认", "按股票", "编号", "做T")):
            continue
        if text in ("买", "卖", "买入", "卖出"):
            continue
        if re.search(r"(买|卖)\d{1,2}:\d{2}:\d{2}", text):
            continue
        if re.search(r"\d{6,}", text):
            continue
        stock_rows.append({"stock_name": text, "y": y})
    stock_rows.sort(key=lambda item: item["y"], reverse=True)
    if not stock_rows:
        return None

    prices = []
    quantities = []
    amounts = []
    details = []
    sides = []
    for item in observations:
        text = clean_ocr_text(item.get("text"))
        x = as_number(item.get("x")) or 0
        y = as_number(item.get("y"))
        if y is None:
            continue
        if 0.32 <= x <= 0.54 and re.search(r"\d+(?:\.\d+)?", text):
            prices.append({"value": as_number(text), "y": y, "text": text})
        elif 0.58 <= x <= 0.76 and re.search(r"\d+", text):
            quantities.append({"value": as_int(text), "y": y, "text": text})
        elif 0.76 <= x <= 1.0 and re.search(r"\d+(?:\.\d+)?", text):
            amounts.append({"value": as_number(text), "y": y, "text": text})
        elif x <= 0.32 and re.search(r"(买|卖)\s*\d{1,2}:\d{2}:\d{2}", text):
            details.append({"text": text, "y": y})
        elif x >= 0.82 and text in ("买入", "卖出", "买", "卖"):
            sides.append({"text": text, "y": y})

    orders = []
    filled_trades = []
    for index, row in enumerate(stock_rows):
        y_top = row["y"]
        y_next = stock_rows[index + 1]["y"] if index + 1 < len(stock_rows) else -1
        y_low = max(y_next + 0.012, y_top - 0.08)
        price_items = [item for item in prices if y_low <= item["y"] <= y_top + 0.025]
        qty_items = [item for item in quantities if y_low <= item["y"] <= y_top + 0.025]
        amount_items = [item for item in amounts if y_low <= item["y"] <= y_top + 0.025]
        detail_items = [item for item in details if y_low <= item["y"] <= y_top + 0.025]
        side_items = [item for item in sides if y_low <= item["y"] <= y_top + 0.025]
        price = price_items[0]["value"] if price_items else None
        quantity = qty_items[0]["value"] if qty_items else None
        amount = amount_items[0]["value"] if amount_items else None
        detail_text = detail_items[0]["text"] if detail_items else ""
        side = normalize_side(detail_text or (side_items[0]["text"] if side_items else ""))
        time_text = detail_text
        is_filled = side in ("buy", "sell") and price is not None and (quantity or 0) > 0
        if amount is None and is_filled:
            amount = round(price * quantity, 3)
        order = {
            "trade_date": None,
            "time": time_text or None,
            "stock_code": None,
            "stock_name": row["stock_name"],
            "side": side,
            "order_price": price,
            "avg_price": price,
            "order_quantity": quantity,
            "filled_quantity": quantity,
            "amount": amount,
            "computed_amount": False,
            "status": "已成" if is_filled else None,
            "is_filled": is_filled,
            "confidence": 0.88,
            "raw_text": " ".join(
                [row["stock_name"], detail_text]
                + [item["text"] for item in price_items + qty_items + amount_items]
            ).strip(),
        }
        orders.append(order)
        if is_filled:
            filled_trades.append(
                {
                    "trade_date": None,
                    "time": time_text or None,
                    "stock_code": None,
                    "stock_name": row["stock_name"],
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "amount": amount,
                    "source_order_index": index,
                }
            )

    if not filled_trades:
        return None
    return {
        "type": "broker_records",
        "recognition_source": "macos_vision_ocr",
        "orders": orders,
        "filled_trades": filled_trades,
        "warnings": ["OCR 快速识别成交明细，请确认成交日期、价格和数量。"],
    }


def try_local_ocr_screenshot(image_path, image_type):
    if image_type == "intraday_sb":
        return None
    ocr = run_local_ocr(image_path)
    if not ocr.get("ok"):
        return {
            "type": "unknown",
            "recognition_source": "macos_vision_ocr",
            "warnings": [f"OCR 快速识别失败：{ocr.get('error')}"],
        }
    observations = ocr.get("observations") or []
    parsed = None
    if image_type in ("auto", "broker_records"):
        parsed = parse_ocr_broker_records(observations)
    if image_type in ("auto", "watchlist_snapshot"):
        parsed = parsed or parse_ocr_board_watchlist(observations) or parse_ocr_stock_list(observations)
    if parsed:
        parsed["ocr_observation_count"] = len(observations)
        return parsed
    return {
        "type": "unknown",
        "recognition_source": "macos_vision_ocr",
        "ocr_observation_count": len(observations),
        "warnings": ["OCR 未解析到可入库结构，已回退到视觉模型。"],
    }


def filled_trades_from_screenshot(screenshot):
    parsed = parse_ai_json(screenshot.get("ocr_json") or "")
    if not isinstance(parsed, dict):
        return []
    if parsed.get("type") != "broker_records":
        return []
    trades = parsed.get("filled_trades")
    if isinstance(trades, list):
        return [trade for trade in trades if isinstance(trade, dict)]
    normalized = parse_ai_json(normalize_broker_result(screenshot.get("ocr_json") or ""))
    if not isinstance(normalized, dict):
        return []
    trades = normalized.get("filled_trades") or []
    return [trade for trade in trades if isinstance(trade, dict)]


def watch_items_from_screenshot(screenshot):
    parsed = parse_ai_json(normalize_watchlist_result(screenshot.get("ocr_json") or ""))
    if not isinstance(parsed, dict) or parsed.get("type") != "watchlist_snapshot":
        return []
    items = parsed.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def import_screenshot_trades(payload):
    screenshot_id = payload.get("screenshot_id")
    if not screenshot_id:
        raise ValueError("screenshot_id is required")
    screenshot = get_screenshot(screenshot_id)
    if not screenshot:
        raise ValueError("screenshot not found")
    trades = payload.get("trades")
    if trades is None:
        trades = filled_trades_from_screenshot(screenshot)
    if not isinstance(trades, list):
        raise ValueError("trades must be a list")

    imported = []
    skipped = []
    for index, trade in enumerate(trades):
        stock_code = str(trade.get("stock_code") or "").strip()
        if not stock_code:
            stock_code = lookup_stock_code(
                trade.get("stock_name"), trade.get("price") or trade.get("avg_price")
            ) or ""
        if not stock_code:
            skipped.append({"index": index, "reason": "stock_code is required", "trade": trade})
            continue
        side = normalize_side(trade.get("side"))
        price = as_number(trade.get("price") or trade.get("avg_price"))
        quantity = as_int(trade.get("quantity") or trade.get("filled_quantity"))
        amount = as_number(trade.get("amount"))
        if amount is None and price is not None and quantity is not None:
            amount = round(price * quantity, 3)
        if side not in ("buy", "sell") or price is None or quantity is None:
            skipped.append({"index": index, "reason": "invalid side/price/quantity", "trade": trade})
            continue
        imported.append(
            create_trade(
                {
                    "trade_date": trade_date_for_screenshot_trade(trade, screenshot),
                    "stock_code": stock_code,
                    "stock_name": trade.get("stock_name") or "",
                    "side": side,
                    "price": price,
                    "quantity": quantity,
                    "amount": amount,
                    "reason": "截图确认导入",
                    "source": "screenshot",
                    "screenshot_id": screenshot_id,
                    "confirmed": True,
                }
            )
        )
    if imported:
        with db() as conn:
            conn.execute(
                "UPDATE screenshots SET imported_at = ? WHERE id = ?",
                (now_iso(), screenshot_id),
            )
    return {"imported": imported, "skipped": skipped}


def import_watchlist_from_screenshot(payload):
    screenshot_id = payload.get("screenshot_id")
    if not screenshot_id:
        raise ValueError("screenshot_id is required")
    screenshot = get_screenshot(screenshot_id)
    if not screenshot:
        raise ValueError("screenshot not found")
    items = payload.get("items")
    if items is None:
        items = watch_items_from_screenshot(screenshot)
    if not isinstance(items, list):
        raise ValueError("items must be a list")

    imported = []
    skipped = []
    for index, item in enumerate(items):
        stock_code = str(item.get("stock_code") or "").strip()
        stock_name = str(item.get("stock_name") or "").strip()
        if not stock_code and stock_name:
            stock_code = lookup_stock_code(stock_name, item.get("last_price")) or ""
        if not stock_code:
            skipped.append({"index": index, "reason": "stock_code is required", "item": item})
            continue
        imported.append(
            upsert_watch(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "reason": (
                        f"截图导入 板块:{item.get('board')} 排名:top{item.get('rank')} "
                        f"涨跌幅:{item.get('pct_change')} 现价:{item.get('last_price')}"
                    ),
                    "sector_name": item.get("board") or "",
                    "sector_rank": item.get("rank"),
                    "strategy_type": "回踩观察",
                }
            )
        )
    if imported:
        with db() as conn:
            conn.execute(
                "UPDATE screenshots SET imported_at = ? WHERE id = ?",
                (now_iso(), screenshot_id),
            )
    return {"imported": imported, "skipped": skipped}


def analyze_and_store_screenshot(image_bytes, ext="jpg", image_type="auto"):
    image_type = normalize_image_type(image_type)
    screenshot_id = str(uuid.uuid4())
    path = UPLOAD_DIR / f"{screenshot_id}.{ext}"
    path.write_bytes(image_bytes)
    ext = ext.lower().lstrip(".")
    image_mime = "image/png" if ext == "png" else "image/jpeg"

    ocr_result = try_local_ocr_screenshot(path, image_type)
    if isinstance(ocr_result, dict) and ocr_result.get("type") == "watchlist_snapshot":
        result_text = json.dumps(ocr_result, ensure_ascii=False, indent=2)
        result_text = normalize_watchlist_result(result_text)
    elif isinstance(ocr_result, dict) and ocr_result.get("type") == "broker_records":
        result_text = json.dumps(ocr_result, ensure_ascii=False, indent=2)
        result_text = normalize_broker_result(result_text)
        result_text = enrich_broker_result_codes(result_text)
    elif isinstance(ocr_result, dict) and image_type in ("auto", "watchlist_snapshot"):
        result_text = json.dumps(ocr_result, ensure_ascii=False, indent=2)
    else:
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = screenshot_prompt(image_type)
        if isinstance(ocr_result, dict) and ocr_result.get("warnings"):
            prompt += "\n\n本地 OCR 快速识别提示：" + json.dumps(
                ocr_result.get("warnings"), ensure_ascii=False
            )
        result = ai_complete([prompt], image_b64=image_b64, image_mime=image_mime)
        result_text = result["text"]
        if image_type == "broker_records":
            result_text = normalize_broker_result(result_text)
            result_text = enrich_broker_result_codes(result_text)
        if image_type == "watchlist_snapshot" or "watchlist_snapshot" in result_text:
            result_text = normalize_watchlist_result(result_text)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO screenshots (id, file_path, image_type, ocr_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (screenshot_id, str(path), image_type, result_text, now_iso()),
        )
    return {
        "id": screenshot_id,
        "file_path": str(path),
        "image_type": image_type,
        "ai_result": result_text,
    }


def save_screenshot_from_data_url(data_url, image_type="auto"):
    if "," in data_url:
        header, encoded = data_url.split(",", 1)
    else:
        header, encoded = "", data_url
    ext = "jpg"
    if "png" in header:
        ext = "png"
    image_bytes = base64.b64decode(encoded)
    return analyze_and_store_screenshot(image_bytes, ext, image_type=image_type)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "TradeReviewAssistant/0.1"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/health":
            json_response(self, {"ok": True, "time": now_iso()})
            return
        if path == "/api/trades":
            if not require_secret(self):
                error_response(self, "unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            json_response(self, {"ok": True, "trades": list_trades(query.get("date", [None])[0])})
            return
        if path == "/api/watchlist":
            if not require_secret(self):
                error_response(self, "unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            json_response(self, {"ok": True, "watchlist": list_watchlist()})
            return
        if path == "/api/screenshots":
            if not require_secret(self):
                error_response(self, "unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            json_response(self, {"ok": True, "screenshots": list_screenshots()})
            return
        if path == "/api/market-context":
            if not require_secret(self):
                error_response(self, "unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            stock_code = query.get("stock_code", [""])[0]
            trade_date = query.get("date", [today_str()])[0]
            if not stock_code:
                error_response(self, "stock_code is required")
                return
            json_response(
                self,
                {
                    "ok": True,
                    "market_context": market_context_for_stock(stock_code, trade_date),
                },
            )
            return
        self.serve_static(path)

    def do_POST(self):
        if not require_secret(self):
            error_response(self, "unauthorized", HTTPStatus.UNAUTHORIZED)
            return
        try:
            payload = parse_body(self)
            if self.path == "/api/trades":
                json_response(self, {"ok": True, "trade": create_trade(payload)})
                return
            if self.path == "/api/watchlist":
                json_response(self, {"ok": True, "watch": upsert_watch(payload)})
                return
            if self.path == "/api/upload-screenshot":
                json_response(
                    self,
                    {
                        "ok": True,
                        "screenshot": save_screenshot_from_data_url(
                            payload["image"], payload.get("image_type", "auto")
                        ),
                    },
                )
                return
            if self.path == "/api/review":
                trade_date = payload.get("trade_date") or today_str()
                json_response(self, {"ok": True, "review": generate_trade_review(trade_date)})
                return
            if self.path == "/api/watch-report":
                json_response(self, {"ok": True, "report": generate_watch_report()})
                return
            if self.path == "/api/sector-report":
                trade_date = payload.get("date") or today_str()
                json_response(
                    self,
                    {"ok": True, "report": generate_sector_rotation_report(trade_date)},
                )
                return
            if self.path == "/api/import-screenshot-trades":
                json_response(self, {"ok": True, **import_screenshot_trades(payload)})
                return
            if self.path == "/api/import-watchlist-screenshot":
                json_response(self, {"ok": True, **import_watchlist_from_screenshot(payload)})
                return
            error_response(self, "not found", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            error_response(self, str(exc), HTTPStatus.BAD_REQUEST)

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        safe = Path(path.lstrip("/"))
        target = (PUBLIC_DIR / safe).resolve()
        if not str(target).startswith(str(PUBLIC_DIR.resolve())) or not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = "text/plain; charset=utf-8"
        if target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[{now_iso()}] {self.address_string()} {fmt % args}")


def telegram_api(method, payload=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = None
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    with urllib.request.urlopen(url, data=data, timeout=70) as response:
        return json.loads(response.read().decode("utf-8"))


def telegram_download_file(file_id):
    file_info = telegram_api("getFile", {"file_id": file_id})
    file_path = file_info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=70) as response:
        return file_path, response.read()


def telegram_send(chat_id, text):
    return telegram_api("sendMessage", {"chat_id": chat_id, "text": text[:3900]})


def screenshot_counts(screenshot):
    parsed = parse_ai_json(screenshot.get("ocr_json") or "") if screenshot else None
    if not isinstance(parsed, dict):
        return {
            "type": "unknown",
            "orders": 0,
            "filled": 0,
            "watch_items": 0,
            "warnings": ["未解析到结构化结果"],
        }
    if parsed.get("type") == "broker_records":
        orders = [item for item in parsed.get("orders") or [] if isinstance(item, dict)]
        filled = [item for item in parsed.get("filled_trades") or [] if isinstance(item, dict)]
        return {
            "type": "broker_records",
            "orders": len(orders),
            "filled": len(filled),
            "watch_items": 0,
            "warnings": parsed.get("warnings") or [],
        }
    if parsed.get("type") == "watchlist_snapshot":
        items = [item for item in parsed.get("items") or [] if isinstance(item, dict)]
        return {
            "type": "watchlist_snapshot",
            "orders": 0,
            "filled": 0,
            "watch_items": len(items),
            "warnings": parsed.get("warnings") or [],
        }
    return {
        "type": parsed.get("type") or "unknown",
        "orders": 0,
        "filled": 0,
        "watch_items": 0,
        "warnings": parsed.get("warnings") or [parsed.get("reason") or "无法识别"],
    }


def format_screenshot_summary(screenshot):
    if not screenshot:
        return "没有找到截图记录。"
    counts = screenshot_counts(screenshot)
    imported = "已入库" if screenshot.get("imported_at") else "待确认"
    lines = [
        f"截图：{screenshot['id']}",
        f"时间：{screenshot.get('created_at')}",
        f"类型：{counts['type']}",
        f"状态：{imported}",
    ]
    if counts["type"] == "broker_records":
        lines.append(f"委托/成交记录：{counts['orders']} 条")
        lines.append(f"真实成交：{counts['filled']} 条")
    elif counts["type"] == "watchlist_snapshot":
        lines.append(f"自选/股票项：{counts['watch_items']} 条")
    if counts["warnings"]:
        lines.append("提示：" + "；".join(str(item) for item in counts["warnings"][:2]))
    return "\n".join(lines)


def telegram_status_text():
    today = today_str()
    with db() as conn:
        screenshot_total = conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE date(created_at) = ?", (today,)
        ).fetchone()[0]
        pending_total = conn.execute(
            "SELECT COUNT(*) FROM screenshots WHERE imported_at IS NULL"
        ).fetchone()[0]
        trade_total = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_date = ?", (today,)
        ).fetchone()[0]
        watch_total = conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE active = 1"
        ).fetchone()[0]
    latest = latest_screenshot()
    return "\n".join(
        [
            f"状态 {today}",
            f"今日截图：{screenshot_total} 张",
            f"今日交易：{trade_total} 笔",
            f"待确认截图：{pending_total} 张",
            f"自选股：{watch_total} 只",
            "",
            "最近截图：",
            format_screenshot_summary(latest),
        ]
    )


def format_trades_for_telegram(trade_date):
    trades = list_trades(trade_date)
    if not trades:
        return f"{trade_date} 没有交易记录。"
    lines = [f"{trade_date} 交易记录："]
    total = 0
    for trade in trades:
        amount = as_number(trade.get("amount")) or 0
        total += amount
        side = "买入" if trade.get("side") == "buy" else "卖出"
        lines.append(
            f"{trade.get('stock_code')} {trade.get('stock_name') or ''} "
            f"{side} {trade.get('price')} x {trade.get('quantity')} = {round(amount, 2)}"
        )
    lines.append(f"合计金额：{round(total, 2)}")
    return "\n".join(lines)


def telegram_import_latest_text():
    screenshot = latest_screenshot(pending_only=True)
    if not screenshot:
        return "没有待确认截图。"
    counts = screenshot_counts(screenshot)
    if counts["type"] == "broker_records":
        result = import_screenshot_trades({"screenshot_id": screenshot["id"]})
        return "\n".join(
            [
                "最近截图导入完成。",
                format_screenshot_summary(get_screenshot(screenshot["id"])),
                f"导入交易：{len(result['imported'])} 笔",
                f"跳过：{len(result['skipped'])} 条",
            ]
        )
    if counts["type"] == "watchlist_snapshot":
        result = import_watchlist_from_screenshot({"screenshot_id": screenshot["id"]})
        return "\n".join(
            [
                "最近截图导入完成。",
                format_screenshot_summary(get_screenshot(screenshot["id"])),
                f"导入自选：{len(result['imported'])} 只",
                f"跳过：{len(result['skipped'])} 条",
            ]
        )
    return "最近一张截图还不能入库：\n" + format_screenshot_summary(screenshot)


def telegram_reload_latest_text():
    screenshot = latest_screenshot()
    if not screenshot:
        return "没有截图可重新解析。"
    path = Path(screenshot.get("file_path") or "")
    if not path.exists():
        return "截图文件不存在，无法重新解析。"
    ext = path.suffix.lstrip(".") or "jpg"
    result = analyze_and_store_screenshot(
        path.read_bytes(),
        ext,
        image_type=screenshot.get("image_type") or "auto",
    )
    reloaded = get_screenshot(result["id"])
    return "已重新解析最近截图，生成新记录：\n" + format_screenshot_summary(reloaded)


def allowed_telegram_user(message):
    if not TELEGRAM_ALLOWED_USER_ID:
        return True
    user = message.get("from", {})
    return str(user.get("id")) == str(TELEGRAM_ALLOWED_USER_ID)


def telegram_help_text():
    return (
        "可用指令：\n"
        "/status 查看今天状态和最近截图\n"
        "/pending 查看最近待确认截图\n"
        "/import_latest 导入最近一张可入库截图\n"
        "/today 查看今天交易\n"
        "/yesterday 查看昨天交易\n"
        "/review 生成昨天交易复盘\n"
        "/review today 生成今天交易复盘\n"
        "/report 生成自选股日报\n"
        "/sector 生成 AI 赛道轮动报告\n"
        "/watch 代码 名称 加入自选\n"
        "/list 查看自选股\n"
        "/reload 重新解析最近一张截图"
    )


def handle_telegram_message(message):
    chat_id = message["chat"]["id"]
    if not allowed_telegram_user(message):
        telegram_send(chat_id, "未授权用户。")
        return
    text = (message.get("text") or "").strip()
    if text.startswith("/start"):
        telegram_send(chat_id, "交易复盘助手已启动。\n\n" + telegram_help_text())
    elif text.startswith("/help"):
        telegram_send(chat_id, telegram_help_text())
    elif text.startswith("/status"):
        telegram_send(chat_id, telegram_status_text())
    elif text.startswith("/pending"):
        telegram_send(chat_id, format_screenshot_summary(latest_screenshot(pending_only=True)))
    elif text.startswith("/import_latest"):
        telegram_send(chat_id, telegram_import_latest_text())
    elif text.startswith("/reload"):
        telegram_send(chat_id, telegram_reload_latest_text())
    elif text.startswith("/today"):
        telegram_send(chat_id, format_trades_for_telegram(today_str()))
    elif text.startswith("/yesterday"):
        telegram_send(chat_id, format_trades_for_telegram(yesterday_str()))
    elif text.startswith("/review"):
        parts = text.split(maxsplit=1)
        target = yesterday_str()
        if len(parts) > 1:
            arg = parts[1].strip().lower()
            if arg in ("today", "今天"):
                target = today_str()
            elif arg in ("yesterday", "昨天"):
                target = yesterday_str()
            elif re.match(r"\d{4}-\d{2}-\d{2}", arg):
                target = arg[:10]
        telegram_send(chat_id, f"开始生成 {target} 交易复盘，请稍等。")
        telegram_send(chat_id, generate_trade_review(target))
    elif text.startswith("/list"):
        items = list_watchlist()
        if not items:
            telegram_send(chat_id, "自选股列表为空。")
        else:
            telegram_send(chat_id, "\n".join([f"{x['stock_code']} {x.get('stock_name') or ''}" for x in items]))
    elif text.startswith("/watch"):
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            telegram_send(chat_id, "用法：/watch 600519 贵州茅台")
            return
        payload = {"stock_code": parts[1], "stock_name": parts[2] if len(parts) > 2 else ""}
        upsert_watch(payload)
        telegram_send(chat_id, f"已加入自选：{payload['stock_code']} {payload['stock_name']}")
    elif text.startswith("/report"):
        telegram_send(chat_id, "开始生成自选股日报，请稍等。")
        telegram_send(chat_id, generate_watch_report())
    elif text.startswith("/sector"):
        telegram_send(chat_id, "开始生成 AI 赛道轮动报告，请稍等。")
        telegram_send(chat_id, generate_sector_rotation_report())
    elif message.get("photo"):
        photos = message["photo"]
        largest = max(photos, key=lambda item: item.get("file_size", 0))
        telegram_send(chat_id, "已收到截图，正在识别。请稍等。")
        try:
            file_path, image_bytes = telegram_download_file(largest["file_id"])
            ext = Path(file_path).suffix.lstrip(".") or "jpg"
            image_type = infer_telegram_image_type(message)
            result = analyze_and_store_screenshot(image_bytes, ext, image_type=image_type)
            screenshot = get_screenshot(result["id"])
            counts = screenshot_counts(screenshot)
            next_action = "发送 /import_latest 可导入，/pending 查看详情。"
            if counts["type"] == "unknown":
                next_action = "未解析到可入库内容，可发送 /reload 重试，或到网页核对。"
            telegram_send(chat_id, format_screenshot_summary(screenshot) + "\n\n" + next_action)
        except Exception as exc:
            telegram_send(chat_id, f"截图识别失败：{exc}")
    else:
        telegram_send(chat_id, "收到。\n\n" + telegram_help_text())


def daily_report_loop():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_REPORT_CHAT_ID or not DAILY_REPORT_TIME:
        return
    last_sent = ""
    while True:
        current = dt.datetime.now()
        stamp = current.strftime("%Y-%m-%d %H:%M")
        if current.strftime("%H:%M") == DAILY_REPORT_TIME and last_sent != stamp:
            try:
                telegram_send(TELEGRAM_REPORT_CHAT_ID, generate_watch_report())
                last_sent = stamp
            except Exception as exc:
                print(f"Daily report error: {exc}")
        time.sleep(30)


def telegram_loop():
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram disabled: TELEGRAM_BOT_TOKEN is empty")
        return
    offset = None
    print("Telegram polling started")
    while True:
        try:
            payload = {"timeout": 60}
            if offset:
                payload["offset"] = offset
            data = telegram_api("getUpdates", payload)
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    handle_telegram_message(message)
        except Exception as exc:
            print(f"Telegram polling error: {exc}")
            time.sleep(5)


def main():
    init_db()
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=daily_report_loop, daemon=True).start()
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), AppHandler)
    print(f"Trade Review Assistant running at http://{APP_HOST}:{APP_PORT}")
    print(f"Use APP_SECRET as the web password. Current value: {APP_SECRET}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

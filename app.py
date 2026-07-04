#!/usr/bin/env python3
import base64
import concurrent.futures
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
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_TIMEOUT_SECONDS = int(os.getenv("MINIMAX_TIMEOUT_SECONDS", "120"))
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
TELEGRAM_SUBSCRIBER_CHAT_IDS = os.getenv("TELEGRAM_SUBSCRIBER_CHAT_IDS", "")
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "15:30")
POSITION_REPORT_TIME = os.getenv("POSITION_REPORT_TIME", "17:35")
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

            CREATE TABLE IF NOT EXISTS positions (
              id TEXT PRIMARY KEY,
              stock_code TEXT NOT NULL UNIQUE,
              stock_name TEXT,
              quantity INTEGER NOT NULL DEFAULT 0,
              cost_price REAL,
              position_type TEXT,
              max_position_pct REAL,
              stop_loss_price REAL,
              take_profit_plan TEXT,
              notes TEXT,
              active INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS position_reports (
              id TEXT PRIMARY KEY,
              report_date TEXT NOT NULL,
              market_data_json TEXT,
              ai_summary TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stock_code_cache (
              stock_name TEXT PRIMARY KEY,
              stock_code TEXT NOT NULL,
              source TEXT,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telegram_subscribers (
              chat_id TEXT PRIMARY KEY,
              user_id TEXT,
              username TEXT,
              first_name TEXT,
              last_name TEXT,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
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
                "text": "DeepSeek 当前配置用于文本分析；图片识别请使用 VISION_AI_PROVIDER=minimax 或 local。"
            }
        return deepseek_chat_completions(messages)
    if provider == "minimax":
        return minimax_chat_completions(messages, image_b64=image_b64, image_mime=image_mime)
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


def minimax_chat_completions(messages, image_b64=None, image_mime="image/jpeg"):
    if not MINIMAX_API_KEY:
        return {"text": "MiniMax 未配置：请在 .env 设置 MINIMAX_API_KEY。"}

    text = "\n\n".join(messages)
    content = [{"type": "text", "text": text}]
    if image_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_mime};base64,{image_b64}",
                    "detail": "default",
                },
            }
        )

    payload = {
        "model": MINIMAX_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_completion_tokens": 3000,
        "thinking": {"type": "disabled"},
    }
    request = urllib.request.Request(
        f"{MINIMAX_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=MINIMAX_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"text": f"MiniMax 请求失败：HTTP {exc.code} {detail}"}
    except Exception as exc:
        return {"text": f"MiniMax 请求失败：{exc}"}

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = json.dumps(data, ensure_ascii=False)
    return {"text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)}


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


def upsert_position(payload):
    now = now_iso()
    item = {
        "id": str(uuid.uuid4()),
        "stock_code": compact_stock_code(payload.get("stock_code")),
        "stock_name": str(payload.get("stock_name") or "").strip(),
        "quantity": as_int(payload.get("quantity")) or 0,
        "cost_price": as_number(payload.get("cost_price")),
        "position_type": str(payload.get("position_type") or "").strip(),
        "max_position_pct": as_number(payload.get("max_position_pct")),
        "stop_loss_price": as_number(payload.get("stop_loss_price")),
        "take_profit_plan": str(payload.get("take_profit_plan") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "active": 1,
        "updated_at": now,
        "created_at": now,
    }
    if not item["stock_code"]:
        raise ValueError("stock_code is required")
    if item["quantity"] < 0:
        raise ValueError("quantity must be non-negative")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO positions
            (id, stock_code, stock_name, quantity, cost_price, position_type,
             max_position_pct, stop_loss_price, take_profit_plan, notes,
             active, updated_at, created_at)
            VALUES
            (:id, :stock_code, :stock_name, :quantity, :cost_price, :position_type,
             :max_position_pct, :stop_loss_price, :take_profit_plan, :notes,
             :active, :updated_at, :created_at)
            ON CONFLICT(stock_code) DO UPDATE SET
              stock_name=COALESCE(NULLIF(excluded.stock_name, ''), positions.stock_name),
              quantity=excluded.quantity,
              cost_price=COALESCE(excluded.cost_price, positions.cost_price),
              position_type=COALESCE(NULLIF(excluded.position_type, ''), positions.position_type),
              max_position_pct=COALESCE(excluded.max_position_pct, positions.max_position_pct),
              stop_loss_price=COALESCE(excluded.stop_loss_price, positions.stop_loss_price),
              take_profit_plan=COALESCE(NULLIF(excluded.take_profit_plan, ''), positions.take_profit_plan),
              notes=COALESCE(NULLIF(excluded.notes, ''), positions.notes),
              active=1,
              updated_at=excluded.updated_at
            """,
            item,
        )
    return item


def list_positions(active_only=True):
    query = "SELECT * FROM positions"
    if active_only:
        query += " WHERE active = 1 AND quantity > 0"
    query += " ORDER BY updated_at DESC, created_at DESC"
    with db() as conn:
        return [row_to_dict(row) for row in conn.execute(query)]


def deactivate_position(stock_code):
    code = compact_stock_code(stock_code)
    if not code:
        raise ValueError("stock_code is required")
    with db() as conn:
        conn.execute(
            "UPDATE positions SET active = 0, quantity = 0, updated_at = ? WHERE stock_code = ?",
            (now_iso(), code),
        )


def compact_position(item):
    return {
        "code": item.get("stock_code"),
        "name": item.get("stock_name"),
        "quantity": item.get("quantity"),
        "cost": item.get("cost_price"),
        "type": item.get("position_type"),
        "max_position_pct": item.get("max_position_pct"),
        "stop_loss": item.get("stop_loss_price"),
        "take_profit_plan": item.get("take_profit_plan"),
        "notes": item.get("notes"),
    }


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


def is_china_market_trading_day(value):
    trade_date = str(value or today_str())
    cal_date = yyyymmdd(trade_date)
    if TUSHARE_TOKEN:
        try:
            import tushare as ts

            pro = ts.pro_api(TUSHARE_TOKEN)
            frame = pro.trade_cal(
                exchange="",
                start_date=cal_date,
                end_date=cal_date,
                fields="cal_date,is_open",
            )
            if frame is not None and not frame.empty:
                return int(frame.iloc[0]["is_open"]) == 1
        except Exception as exc:
            print(f"Trading calendar check failed, falling back to K-line date: {exc}")

    context = market_context_for_stock("000001", trade_date)
    return bool(context.get("ok") and context.get("k_date") == trade_date)


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
        adj = pro.adj_factor(
            ts_code=tushare_ts_code(stock_code),
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception as exc:
        return {"ok": False, "error": f"Tushare 拉取失败：{exc}"}
    if frame is None or frame.empty:
        return {"ok": False, "error": f"Tushare 未返回 {stock_code} 的日 K 数据"}
    frame = normalize_tushare_frame(frame)
    if adj is not None and not adj.empty:
        adj = adj.rename(columns={"trade_date": "日期"}).copy()
        adj["日期"] = adj["日期"].astype(str).str.replace(
            r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True
        )
        adj = adj.sort_values("日期")
        frame = frame.merge(adj[["日期", "adj_factor"]], on="日期", how="left")
        latest_adj = frame["adj_factor"].dropna().iloc[-1] if frame["adj_factor"].notna().any() else None
        if latest_adj:
            ratio = frame["adj_factor"] / latest_adj
            for col in ("开盘", "收盘", "最高", "最低"):
                if col in frame.columns:
                    frame[col] = frame[col].astype(float) * ratio
    return {"ok": True, "frame": frame, "provider": "tushare_qfq"}


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
    prev_close = closes[-2] if len(closes) >= 2 else None
    pct_change = (
        round((close - prev_close) / prev_close * 100, 4)
        if close is not None and prev_close not in (None, 0)
        else as_number(latest.get("涨跌幅"))
    )

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
        "pct_change": pct_change,
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
            "code": context.get("stock_code"),
            "date": context.get("k_date"),
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


def watch_market_contexts(watch_items, report_date):
    items = [item for item in watch_items if item.get("stock_code")]
    if not items:
        return []

    def load(item):
        return market_context_for_stock(item["stock_code"], report_date)

    max_workers = min(12, max(1, len(items)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(load, items))


def watch_reports_before(report_date, limit=60):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT report_date, created_at, market_data_json, ai_summary
            FROM daily_stock_reports
            WHERE report_date < ?
            ORDER BY report_date DESC, created_at DESC
            LIMIT ?
            """,
            (report_date, limit),
        ).fetchall()
    reports = []
    for row in rows:
        try:
            market_data = json.loads(row["market_data_json"] or "{}")
        except Exception:
            market_data = {}
        if market_data.get("type") == "watch_report":
            reports.append(
                {
                    "report_date": row["report_date"],
                    "created_at": row["created_at"],
                    "market_data": market_data,
                    "ai_summary": row["ai_summary"] or "",
                }
            )
    return reports


def watch_report_exists(report_date):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT market_data_json
            FROM daily_stock_reports
            WHERE report_date = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (report_date,),
        ).fetchall()
    for row in rows:
        try:
            market_data = json.loads(row["market_data_json"] or "{}")
        except Exception:
            market_data = {}
        if market_data.get("type") == "watch_report":
            return True
    return False


def latest_watch_report_before(report_date):
    reports = watch_reports_before(report_date, limit=60)
    return reports[0] if reports else None


def watch_report_uses_own_market_date(report):
    market_data = (report or {}).get("market_data") or {}
    coverage = market_data.get("coverage") or {}
    report_date = (report or {}).get("report_date")
    data_dates = coverage.get("data_dates") or []
    stale = coverage.get("stale")
    if report_date and data_dates:
        return report_date in data_dates and stale in (0, None)
    return True


def extract_focus_items_from_market_data(market_data):
    if not isinstance(market_data, dict):
        return []
    for key in ("focus_items", "selected_focus_items"):
        items = market_data.get(key)
        if isinstance(items, list) and items:
            normalized = []
            seen = set()
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = compact_stock_code(item.get("code") or item.get("stock_code"))
                if not code or code in seen:
                    continue
                seen.add(code)
                normalized.append({"code": code, "name": item.get("name") or item.get("stock_name")})
            if normalized:
                return normalized
    return []


def extract_focus_codes_from_report_text(text):
    if not text:
        return []
    start_match = re.search(r"(?:#{1,6}\s*)?(?:\*\*)?重点观察(?:\*\*)?", text)
    if not start_match:
        return []
    start = start_match.start()
    end_match = re.search(r"(?:#{1,6}\s*)?(?:\*\*)?(?:继续跟踪|暂时回避)(?:\*\*)?", text[start + 1 :])
    end = start + 1 + end_match.start() if end_match else len(text)
    section = text[start:end]
    seen = set()
    items = []
    patterns = [
        r"(?:^|\n)\s*(?:[-*]\s*)?(?:\d+[.)、]\s*)?\*{0,2}\s*([A-Za-z0-9\u4e00-\u9fff\-\s]+?)\s*[\(（]\s*(\d{6})\s*[\)）]",
        r"([A-Za-z0-9\u4e00-\u9fff\-]+)\s*[\(（]\s*(\d{6})\s*[\)）]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, section):
            name = re.sub(r"^[\s\d.)、*-]+|[\s*:：*-]+$", "", match.group(1)).strip()
            code = match.group(2)
            if code in seen:
                continue
            seen.add(code)
            items.append({"code": code, "name": name})
    return items


def extract_focus_items_from_context(focus_codes, compact_items):
    metadata = {
        compact_stock_code(item.get("code")): item
        for item in compact_items
        if item.get("code")
    }
    normalized = []
    seen = set()
    for item in focus_codes:
        code = compact_stock_code(item.get("code"))
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        meta = metadata.get(code) or {}
        normalized.append(
            {
                "code": code,
                "name": item.get("name") or meta.get("name"),
                "sector": item.get("sector") or meta.get("sector"),
                "rank": item.get("rank") or meta.get("rank"),
            }
        )
    return normalized


def previous_focus_review(report_date):
    previous = None
    focus_items = []
    for candidate in watch_reports_before(report_date, limit=60):
        if not watch_report_uses_own_market_date(candidate):
            continue
        market_data = candidate.get("market_data") or {}
        compact_items = market_data.get("watch_items") or []
        focus_items = extract_focus_items_from_market_data(market_data)
        if not focus_items:
            focus_items = extract_focus_items_from_context(
                extract_focus_codes_from_report_text(candidate.get("ai_summary") or ""),
                compact_items,
            )
        if focus_items:
            previous = candidate
            break
        if previous is None:
            previous = candidate
    if not previous:
        return None
    if not focus_items:
        return {
            "previous_report_date": previous["report_date"],
            "focus_items": [],
            "today_context": [],
        }
    today_context = []
    for item in focus_items[:12]:
        context = compact_market_context(market_context_for_stock(item["code"], report_date))
        context.update(
            {
                "name": item.get("name"),
                "sector": item.get("sector"),
                "rank": item.get("rank"),
            }
        )
        today_context.append(context)
    return {
        "previous_report_date": previous["report_date"],
        "previous_created_at": previous.get("created_at"),
        "focus_items": focus_items[:12],
        "today_context": today_context,
    }


def enrich_watch_market_contexts(watch_items, market_context):
    metadata = {
        compact_stock_code(item.get("stock_code")): item
        for item in watch_items
        if item.get("stock_code")
    }
    enriched = []
    for context in market_context:
        compact = compact_market_context(context)
        code = compact_stock_code(compact.get("code") or compact.get("stock_code"))
        item = metadata.get(code) or {}
        compact.update(
            {
                "name": item.get("stock_name"),
                "sector": item.get("sector_name") or "未分组",
                "rank": item.get("sector_rank"),
                "strategy": item.get("strategy_type"),
            }
        )
        enriched.append(compact)
    return enriched


def generate_watch_report(report_date=None):
    watch_items = list_watchlist()
    report_date = report_date or today_str()
    market_context = watch_market_contexts(watch_items, report_date)
    focus_review = previous_focus_review(report_date)
    compact_items = [compact_watch_item(item) for item in watch_items]
    compact_context = enrich_watch_market_contexts(watch_items, market_context)
    ok_context = [item for item in compact_context if item.get("ok")]
    missing_context = [item for item in compact_context if not item.get("ok")]
    stale_context = [
        item
        for item in ok_context
        if item.get("date") and item.get("date") != report_date
    ]
    data_dates = sorted({item.get("date") for item in ok_context if item.get("date")})
    coverage = {
        "total": len(compact_context),
        "ok": len(ok_context),
        "missing": len(missing_context),
        "missing_items": missing_context,
        "data_dates": data_dates,
        "stale": len(stale_context),
        "stale_items": stale_context,
    }
    prompt = (
        "你是一个自选股盘后观察助手。不要给直接买入建议，不要预测确定收益。\n"
        "基于用户预设条件和本地计算的 K 线摘要输出盘后观察。\n"
        "请第一行明确写出行情覆盖率，例如“行情覆盖：82/82”。\n"
        "请第二行明确写出行情实际日期。如果行情实际日期早于报告日期，必须说明“当前数据源尚未更新到报告日期”，"
        "并且不要把旧日期数据表述为今日涨跌。\n"
        "如果提供了昨日重点关注回顾，请先输出“昨日重点关注回顾”小节，简明说明昨天重点票今天哪些兑现、哪些转弱、哪些继续观察。\n"
        "如果缺失行情，只列出缺失股票；如果未缺失，请明确说明“未详细展开的股票不代表数据缺失”。\n"
        "不要逐股平铺全部自选股，先筛选最值得明日观察的 8-12 只，再按三类输出：重点观察、继续跟踪、暂时回避。\n"
        "每只入选股票标题必须使用“名称（代码｜概念）”格式；概念优先使用 K线摘要或自选股里的 sector 字段，缺失时写“未分组”。\n"
        "每个入选股票说明具体依据：涨跌幅、MA5/MA10/MA20、量能相对 20 日均量、是否有企稳/过热/破位迹象。\n"
        "请直接使用提供的精确数值，不要写“估算”“约”“大约”。\n"
        "总字数控制在 1400 字以内。\n"
        f"日期：{report_date}\n"
        f"行情覆盖：{json.dumps(coverage, ensure_ascii=False)}\n"
        f"昨日重点关注回顾数据：{json.dumps(focus_review, ensure_ascii=False)}\n"
        f"自选股：{json.dumps(compact_items, ensure_ascii=False)}\n"
        f"K线摘要：{json.dumps(compact_context, ensure_ascii=False)}"
    )
    result = ai_complete([prompt])
    focus_items = extract_focus_items_from_context(
        extract_focus_codes_from_report_text(result["text"]),
        compact_items,
    )
    report_id = str(uuid.uuid4())
    market_data = {
        "type": "watch_report",
        "coverage": coverage,
        "previous_focus_review": focus_review,
        "focus_items": focus_items,
        "watch_items": compact_items,
        "market_context": compact_context,
    }
    with db() as conn:
        conn.execute(
            """
            INSERT INTO daily_stock_reports
            (id, report_date, market_data_json, ai_summary, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report_id,
                report_date,
                json.dumps(market_data, ensure_ascii=False),
                result["text"],
                now_iso(),
            ),
        )
    return result["text"]


def position_report_exists(report_date):
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM position_reports WHERE report_date = ? LIMIT 1",
            (report_date,),
        ).fetchone()
    return bool(row)


def enrich_position_market_contexts(positions, report_date):
    enriched = []
    for position in positions:
        context = compact_market_context(
            market_context_for_stock(position.get("stock_code"), report_date)
        )
        cost = as_number(position.get("cost_price"))
        close = as_number(context.get("close"))
        quantity = as_int(position.get("quantity")) or 0
        pnl_pct = (
            round((close - cost) / cost * 100, 4)
            if close is not None and cost not in (None, 0)
            else None
        )
        market_value = round(close * quantity, 2) if close is not None else None
        context.update(
            {
                "name": position.get("stock_name"),
                "quantity": quantity,
                "cost": cost,
                "pnl_pct": pnl_pct,
                "market_value": market_value,
                "position_type": position.get("position_type"),
                "max_position_pct": position.get("max_position_pct"),
                "stop_loss": position.get("stop_loss_price"),
                "take_profit_plan": position.get("take_profit_plan"),
                "notes": position.get("notes"),
            }
        )
        enriched.append(context)
    return enriched


def generate_position_report(report_date=None):
    report_date = report_date or today_str()
    positions = list_positions()
    if not positions:
        return "当前没有持仓记录。可用 /position 代码 名称 数量 成本价 添加。"
    compact_positions = [compact_position(item) for item in positions]
    market_context = enrich_position_market_contexts(positions, report_date)
    ok_context = [item for item in market_context if item.get("ok")]
    missing_context = [item for item in market_context if not item.get("ok")]
    data_dates = sorted({item.get("date") for item in ok_context if item.get("date")})
    coverage = {
        "total": len(market_context),
        "ok": len(ok_context),
        "missing": len(missing_context),
        "missing_items": missing_context,
        "data_dates": data_dates,
    }
    prompt = (
        "你是一个只服务用户本人的持仓风控复盘助手。不要承诺收益，不要给确定性荐股。\n"
        "请基于用户持仓、成本和本地计算的 K 线摘要，输出私有持仓日报。\n"
        "请第一行写“私有持仓日报”，第二行写行情覆盖和行情实际日期。\n"
        "重点关注：仓位风险、成本线压力、是否跌破关键均线、是否放量下跌、是否过热、止损/止盈执行提醒。\n"
        "请按三类输出：需要处理、继续持有观察、明日条件单/观察点。\n"
        "每只股票必须写清：名称（代码）、当前价、成本价、浮盈亏比例、MA5/MA10/MA20、量能相对20日均量。\n"
        "如果缺失行情，请明确列出，不要猜测。\n"
        "总字数控制在 1400 字以内，语气克制、具体。\n"
        f"日期：{report_date}\n"
        f"行情覆盖：{json.dumps(coverage, ensure_ascii=False)}\n"
        f"持仓：{json.dumps(compact_positions, ensure_ascii=False)}\n"
        f"K线与盈亏摘要：{json.dumps(market_context, ensure_ascii=False)}"
    )
    result = ai_complete([prompt])
    report_id = str(uuid.uuid4())
    market_data = {
        "type": "position_report",
        "coverage": coverage,
        "positions": compact_positions,
        "market_context": market_context,
    }
    with db() as conn:
        conn.execute(
            """
            INSERT INTO position_reports
            (id, report_date, market_data_json, ai_summary, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report_id,
                report_date,
                json.dumps(market_data, ensure_ascii=False),
                result["text"],
                now_iso(),
            ),
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


def estimate_sector_kelly(ok_stocks, above_ma5, above_ma20, near_ma5, avg_pct):
    total = max(len(ok_stocks), 1)
    ma5_ratio = above_ma5 / total
    ma20_ratio = above_ma20 / total
    near_ma5_ratio = near_ma5 / total
    volume_values = [
        stock.get("vol20")
        for stock in ok_stocks
        if isinstance(stock.get("vol20"), (int, float))
    ]
    avg_vol20 = sum(volume_values) / len(volume_values) if volume_values else 1.0

    win_rate = 0.32
    win_rate += 0.18 * ma5_ratio
    win_rate += 0.12 * ma20_ratio
    win_rate += 0.08 * near_ma5_ratio
    if avg_pct is not None:
        if -2.5 <= avg_pct <= 1.5:
            win_rate += 0.06
        elif avg_pct > 4:
            win_rate -= 0.05
        elif avg_pct < -5:
            win_rate -= 0.08
        elif avg_pct < -3:
            win_rate -= 0.04
    if 0.8 <= avg_vol20 <= 1.8:
        win_rate += 0.04
    elif avg_vol20 > 2.5:
        win_rate -= 0.04
    win_rate = min(max(win_rate, 0.15), 0.56)

    payoff_ratio = 1.2
    payoff_ratio += 0.55 * ma20_ratio
    payoff_ratio += 0.35 * near_ma5_ratio
    if avg_pct is not None:
        if avg_pct < -4:
            payoff_ratio += 0.1
        elif avg_pct > 5:
            payoff_ratio -= 0.25
    payoff_ratio = min(max(payoff_ratio, 0.8), 3.0)

    full_kelly = win_rate - (1 - win_rate) / payoff_ratio if payoff_ratio > 0 else 0
    full_kelly = max(full_kelly, 0)
    conservative_kelly = full_kelly * 0.25
    conservative_cap = min(conservative_kelly, 0.05)
    if conservative_cap < 0.015:
        level = "只看不动"
    elif conservative_cap < 0.04:
        level = "轻仓观察"
    elif conservative_cap < 0.045:
        level = "标准观察"
    else:
        level = "高质量轻仓观察"
    return {
        "estimated_win_rate": round(win_rate, 3),
        "estimated_payoff_ratio": round(payoff_ratio, 3),
        "full_kelly": round(full_kelly, 4),
        "quarter_kelly": round(conservative_kelly, 4),
        "conservative_position_cap": round(conservative_cap, 4),
        "position_level": level,
        "method": "rule_based_quarter_kelly",
    }


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
        avg_pct = round(sum(pct_values) / len(pct_values), 3) if pct_values else None
        kelly = estimate_sector_kelly(ok_stocks, above_ma5, above_ma20, near_ma5, avg_pct)
        sectors.append(
            {
                "sector": sector,
                "ok": True,
                "stock_count": len(ok_stocks),
                "avg_pct": avg_pct,
                "above_ma5": above_ma5,
                "above_ma20": above_ma20,
                "near_ma5": near_ma5,
                "kelly": kelly,
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
        "4. 凯利仓位参考：使用赛道快照里的 kelly 字段，只输出保守仓位上限，不输出满仓建议；"
        "请说明这是规则估算，不是确定胜率。\n"
        "5. 明日观察触发条件：用可执行条件表达，例如“赛道内 top4 至少 2 只站上 MA5”。\n"
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
        "position": "position_snapshot",
        "positions": "position_snapshot",
        "holding": "position_snapshot",
        "holdings": "position_snapshot",
        "持仓": "position_snapshot",
        "仓位": "position_snapshot",
        "持仓股": "position_snapshot",
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
    if image_type == "position_snapshot":
        return (
            common
            + "这类图片是券商/App 的持仓列表截图，通常每行包含股票名称、代码、持仓数量、成本价、现价、市值、盈亏或盈亏比例。\n"
            "请只提取真实持仓股票，不要提取指数、自选栏目、按钮、账户汇总行。股票代码通常是 6 位数字；看不清的字段填 null。\n"
            "如果表头是“市值、持有盈亏、当日盈亏、成本/现价”，每行左侧第一行是股票名称、第二行是市值；"
            "最右侧“成本/现价”单元格第一行是 cost_price，第二行是 last_price，必须分别提取。\n"
            "quantity 必须是持仓数量/股票数量，不要用可买数量、成交金额或市值代替；如果图片没有直接显示数量，填 null，后续系统会用市值/现价估算。\n"
            "返回结构：{\"type\":\"position_snapshot\",\"positions\":[{\"stock_code\":null,\"stock_name\":null,"
            "\"quantity\":null,\"cost_price\":null,\"last_price\":null,\"market_value\":null,\"pnl\":null,"
            "\"pnl_pct\":null,\"position_type\":null,\"confidence\":0到1,\"raw_text\":null}],\"warnings\":[]}"
        )
    return (
        common
        + "请先判断截图类型：intraday_sb（日内图上 S/B 点）、broker_records（券商成交记录列表）"
        "、watchlist_snapshot（自选股列表）或 position_snapshot（持仓列表）。\n"
        "如果是日内 S/B 点，按 intraday_sb 结构输出；如果是券商成交记录合集，按 broker_records 结构输出；"
        "如果是自选股列表，按 watchlist_snapshot 结构输出；如果是持仓列表，按 position_snapshot 结构输出。"
    )


def infer_telegram_image_type(message):
    caption = (message.get("caption") or "").strip().lower()
    if any(token in caption for token in ("日内", "分时", "sb", "s/b", "k线", "kline", "intraday")):
        return "intraday_sb"
    if any(token in caption for token in ("持仓", "仓位", "持仓股", "position", "positions", "holding", "holdings")):
        return "position_snapshot"
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
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if value.endswith("%"):
            value = value[:-1].strip()
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


def normalize_position_result(text):
    parsed = parse_ai_json(text)
    if not isinstance(parsed, dict) or parsed.get("type") != "position_snapshot":
        return text
    raw_positions = parsed.get("positions")
    if raw_positions is None:
        raw_positions = parsed.get("items") or parsed.get("data") or parsed.get("stocks") or []
    if not isinstance(raw_positions, list):
        return text

    positions = []
    for raw in raw_positions:
        if not isinstance(raw, dict):
            continue
        stock_code = compact_stock_code(
            raw.get("stock_code")
            or raw.get("code")
            or raw.get("证券代码")
            or raw.get("代码")
        )
        stock_name = str(
            raw.get("stock_name")
            or raw.get("name")
            or raw.get("证券名称")
            or raw.get("名称")
            or ""
        ).strip()
        quantity = as_int(
            raw.get("quantity")
            if raw.get("quantity") is not None
            else raw.get("shares")
            if raw.get("shares") is not None
            else raw.get("持仓数量")
            if raw.get("持仓数量") is not None
            else raw.get("股票余额")
        )
        cost_price = as_number(
            raw.get("cost_price")
            if raw.get("cost_price") is not None
            else raw.get("cost")
            if raw.get("cost") is not None
            else raw.get("成本价")
            if raw.get("成本价") is not None
            else raw.get("持仓成本")
        )
        last_price = as_number(
            raw.get("last_price")
            if raw.get("last_price") is not None
            else raw.get("price")
            if raw.get("price") is not None
            else raw.get("现价")
            if raw.get("现价") is not None
            else raw.get("最新价")
        )
        market_value = as_number(
            raw.get("market_value")
            if raw.get("market_value") is not None
            else raw.get("市值")
            if raw.get("市值") is not None
            else raw.get("持仓市值")
        )
        pnl = as_number(
            raw.get("pnl")
            if raw.get("pnl") is not None
            else raw.get("profit")
            if raw.get("profit") is not None
            else raw.get("盈亏")
            if raw.get("盈亏") is not None
            else raw.get("浮动盈亏")
        )
        pnl_pct = as_number(
            raw.get("pnl_pct")
            if raw.get("pnl_pct") is not None
            else raw.get("profit_pct")
            if raw.get("profit_pct") is not None
            else raw.get("盈亏比例")
            if raw.get("盈亏比例") is not None
            else raw.get("收益率")
        )
        positions.append(
            {
                "stock_code": stock_code or None,
                "stock_name": stock_name or None,
                "quantity": quantity,
                "cost_price": cost_price,
                "last_price": last_price,
                "market_value": market_value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "position_type": str(raw.get("position_type") or raw.get("类型") or "").strip() or None,
                "confidence": raw.get("confidence"),
                "raw_text": str(raw.get("raw_text") or raw.get("原文") or "").strip() or None,
            }
        )
    normalized = {
        "type": "position_snapshot",
        "positions": positions,
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


def positions_from_screenshot(screenshot):
    parsed = parse_ai_json(normalize_position_result(screenshot.get("ocr_json") or ""))
    if not isinstance(parsed, dict) or parsed.get("type") != "position_snapshot":
        return []
    positions = parsed.get("positions") or []
    return [item for item in positions if isinstance(item, dict)]


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


def import_positions_from_screenshot(payload):
    screenshot_id = payload.get("screenshot_id")
    if not screenshot_id:
        raise ValueError("screenshot_id is required")
    screenshot = get_screenshot(screenshot_id)
    if not screenshot:
        raise ValueError("screenshot not found")
    positions = payload.get("positions")
    if positions is None:
        positions = positions_from_screenshot(screenshot)
    if not isinstance(positions, list):
        raise ValueError("positions must be a list")

    imported = []
    skipped = []
    for index, item in enumerate(positions):
        if not isinstance(item, dict):
            skipped.append({"index": index, "reason": "invalid position item", "item": item})
            continue
        stock_code = compact_stock_code(item.get("stock_code"))
        stock_name = str(item.get("stock_name") or "").strip()
        last_price = as_number(item.get("last_price"))
        market_value = as_number(item.get("market_value"))
        cost_price = as_number(item.get("cost_price"))
        quantity = as_int(item.get("quantity"))
        quantity_estimated = False
        if (quantity is None or quantity <= 0) and market_value is not None and last_price:
            estimated_quantity = round(market_value / last_price)
            if estimated_quantity > 0:
                quantity = estimated_quantity
                quantity_estimated = True
        if quantity is None or quantity <= 0:
            skipped.append({"index": index, "reason": "quantity must be positive", "item": item})
            continue
        if not stock_code and stock_name:
            stock_code = lookup_stock_code(stock_name, last_price or cost_price) or ""
        if not stock_code:
            skipped.append({"index": index, "reason": "stock_code is required", "item": item})
            continue
        note_parts = ["持仓截图导入"]
        if quantity_estimated:
            note_parts.append("数量按市值/现价估算")
        for label, value in (
            ("现价", last_price),
            ("市值", market_value),
            ("盈亏", as_number(item.get("pnl"))),
            ("盈亏率", as_number(item.get("pnl_pct"))),
        ):
            if value is not None:
                note_parts.append(f"{label}:{value}")
        raw_text = str(item.get("raw_text") or "").strip()
        if raw_text:
            note_parts.append(f"原文:{raw_text[:80]}")
        imported.append(
            upsert_position(
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "quantity": quantity,
                    "cost_price": cost_price,
                    "position_type": item.get("position_type") or "截图持仓",
                    "notes": " ".join(note_parts),
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
    elif isinstance(ocr_result, dict) and ocr_result.get("type") == "position_snapshot":
        result_text = json.dumps(ocr_result, ensure_ascii=False, indent=2)
        result_text = normalize_position_result(result_text)
    elif (
        isinstance(ocr_result, dict)
        and ocr_result.get("type") != "unknown"
        and image_type in ("auto", "watchlist_snapshot", "position_snapshot")
    ):
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
        if image_type == "position_snapshot" or "position_snapshot" in result_text:
            result_text = normalize_position_result(result_text)
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
                report_date = payload.get("date") or payload.get("report_date") or today_str()
                json_response(self, {"ok": True, "report": generate_watch_report(report_date)})
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
            if self.path == "/api/import-position-screenshot":
                json_response(self, {"ok": True, **import_positions_from_screenshot(payload)})
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
            "positions": 0,
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
            "positions": 0,
            "warnings": parsed.get("warnings") or [],
        }
    if parsed.get("type") == "watchlist_snapshot":
        items = [item for item in parsed.get("items") or [] if isinstance(item, dict)]
        return {
            "type": "watchlist_snapshot",
            "orders": 0,
            "filled": 0,
            "watch_items": len(items),
            "positions": 0,
            "warnings": parsed.get("warnings") or [],
        }
    if parsed.get("type") == "position_snapshot":
        positions = [item for item in parsed.get("positions") or [] if isinstance(item, dict)]
        return {
            "type": "position_snapshot",
            "orders": 0,
            "filled": 0,
            "watch_items": 0,
            "positions": len(positions),
            "warnings": parsed.get("warnings") or [],
        }
    return {
        "type": parsed.get("type") or "unknown",
        "orders": 0,
        "filled": 0,
        "watch_items": 0,
        "positions": 0,
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
    elif counts["type"] == "position_snapshot":
        lines.append(f"持仓项：{counts['positions']} 条")
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
    if counts["type"] == "position_snapshot":
        result = import_positions_from_screenshot({"screenshot_id": screenshot["id"]})
        return "\n".join(
            [
                "最近截图导入完成。",
                format_screenshot_summary(get_screenshot(screenshot["id"])),
                f"导入持仓：{len(result['imported'])} 只",
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


def is_telegram_owner(message):
    return allowed_telegram_user(message)


def register_telegram_subscriber(message, active=1):
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return None
    now = now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO telegram_subscribers
            (chat_id, user_id, username, first_name, last_name, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
              user_id=excluded.user_id,
              username=excluded.username,
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              active=excluded.active,
              updated_at=excluded.updated_at
            """,
            (
                chat_id,
                str(user.get("id") or ""),
                str(user.get("username") or ""),
                str(user.get("first_name") or ""),
                str(user.get("last_name") or ""),
                active,
                now,
                now,
            ),
        )
    return chat_id


def list_telegram_subscriber_chat_ids():
    chat_ids = []
    for raw in TELEGRAM_SUBSCRIBER_CHAT_IDS.split(","):
        value = raw.strip()
        if value:
            chat_ids.append(value)
    with db() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM telegram_subscribers WHERE active=1 ORDER BY created_at"
        ).fetchall()
    chat_ids.extend(str(row["chat_id"]) for row in rows)
    return chat_ids


def telegram_report_recipient_ids():
    recipients = []
    if TELEGRAM_REPORT_CHAT_ID:
        recipients.append(str(TELEGRAM_REPORT_CHAT_ID))
    recipients.extend(list_telegram_subscriber_chat_ids())
    seen = set()
    unique = []
    for chat_id in recipients:
        if chat_id and chat_id not in seen:
            unique.append(chat_id)
            seen.add(chat_id)
    return unique


def telegram_broadcast_report(text):
    sent = []
    failed = []
    for chat_id in telegram_report_recipient_ids():
        try:
            telegram_send(chat_id, text)
            sent.append(chat_id)
        except Exception as exc:
            failed.append({"chat_id": chat_id, "error": str(exc)})
    return {"sent": sent, "failed": failed}


def telegram_owner_recipient_ids():
    recipients = []
    if TELEGRAM_ALLOWED_USER_ID:
        recipients.append(str(TELEGRAM_ALLOWED_USER_ID))
    seen = set()
    unique = []
    for chat_id in recipients:
        if chat_id and chat_id not in seen:
            unique.append(chat_id)
            seen.add(chat_id)
    return unique


def telegram_send_owner_only(text):
    sent = []
    failed = []
    for chat_id in telegram_owner_recipient_ids():
        try:
            telegram_send(chat_id, text)
            sent.append(chat_id)
        except Exception as exc:
            failed.append({"chat_id": chat_id, "error": str(exc)})
    return {"sent": sent, "failed": failed}


def format_positions_for_telegram():
    positions = list_positions()
    if not positions:
        return "当前没有持仓记录。"
    lines = ["当前私有持仓："]
    for item in positions:
        parts = [
            item.get("stock_code") or "",
            item.get("stock_name") or "",
            f"数量 {item.get('quantity')}",
        ]
        if item.get("cost_price") is not None:
            parts.append(f"成本 {item.get('cost_price')}")
        if item.get("position_type"):
            parts.append(str(item.get("position_type")))
        if item.get("stop_loss_price") is not None:
            parts.append(f"止损 {item.get('stop_loss_price')}")
        lines.append(" ".join(str(part) for part in parts if part != ""))
    return "\n".join(lines)


def telegram_upsert_position_text(text):
    parts = text.split(maxsplit=5)
    if len(parts) < 5:
        return "用法：/position 300308 中际旭创 200 128.5 核心持仓"
    payload = {
        "stock_code": parts[1],
        "stock_name": parts[2],
        "quantity": parts[3],
        "cost_price": parts[4],
        "position_type": parts[5] if len(parts) > 5 else "",
    }
    item = upsert_position(payload)
    return (
        "已更新私有持仓："
        f"{item['stock_code']} {item['stock_name']} "
        f"数量 {item['quantity']} 成本 {item.get('cost_price')}"
    )


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
        "/report yesterday 重算昨天自选股日报\n"
        "/sector 生成 AI 赛道轮动报告\n"
        "/watch 代码 名称 加入自选\n"
        "/list 查看自选股\n"
        "/position 代码 名称 数量 成本价 维护私有持仓\n"
        "持仓截图 caption 写“持仓”后发送，再用 /import_latest 导入\n"
        "/positions 查看私有持仓\n"
        "/position_report 生成私有持仓日报\n"
        "/position_remove 代码 删除私有持仓\n"
        "/reload 重新解析最近一张截图"
    )


def parse_telegram_date_arg(text, default_date):
    parts = text.split(maxsplit=1)
    if len(parts) <= 1:
        return default_date
    arg = parts[1].strip().lower()
    if arg in ("today", "今天"):
        return today_str()
    if arg in ("yesterday", "昨天"):
        return yesterday_str()
    if re.match(r"\d{4}-\d{2}-\d{2}", arg):
        return arg[:10]
    return default_date


def handle_telegram_message(message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not is_telegram_owner(message):
        if text.startswith("/stop"):
            register_telegram_subscriber(message, active=0)
            telegram_send(chat_id, "已取消每日 17:30 自选股日报订阅。")
            return
        register_telegram_subscriber(message, active=1)
        if text.startswith("/start") or text.startswith("/subscribe"):
            telegram_send(chat_id, "已订阅每日 17:30 自选股日报。这个机器人不开放指令操作。")
        return
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
        target = parse_telegram_date_arg(text, yesterday_str())
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
    elif text.startswith("/positions"):
        telegram_send(chat_id, format_positions_for_telegram())
    elif text.startswith("/position_remove"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            telegram_send(chat_id, "用法：/position_remove 300308")
            return
        deactivate_position(parts[1])
        telegram_send(chat_id, f"已删除私有持仓：{compact_stock_code(parts[1])}")
    elif text.startswith("/position_report"):
        target = parse_telegram_date_arg(text, today_str())
        telegram_send(chat_id, f"开始生成 {target} 私有持仓日报，只会发送给你本人。")
        telegram_send(chat_id, generate_position_report(target))
    elif text.startswith("/position"):
        try:
            telegram_send(chat_id, telegram_upsert_position_text(text))
        except Exception as exc:
            telegram_send(chat_id, f"持仓更新失败：{exc}")
    elif text.startswith("/report"):
        target = parse_telegram_date_arg(text, today_str())
        telegram_send(chat_id, f"开始生成 {target} 自选股日报，请稍等。")
        telegram_send(chat_id, generate_watch_report(target))
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
    if not TELEGRAM_BOT_TOKEN or not DAILY_REPORT_TIME:
        print("Daily report disabled: TELEGRAM_BOT_TOKEN or DAILY_REPORT_TIME is empty", flush=True)
        return
    last_sent_date = ""
    try:
        scheduled_hour, scheduled_minute = [int(part) for part in DAILY_REPORT_TIME.split(":", 1)]
        scheduled_time = dt.time(scheduled_hour, scheduled_minute)
    except Exception:
        print(f"Daily report disabled: invalid DAILY_REPORT_TIME={DAILY_REPORT_TIME}", flush=True)
        return
    current = dt.datetime.now()
    if current.time() >= scheduled_time and watch_report_exists(current.date().isoformat()):
        last_sent_date = current.date().isoformat()
    print(f"Daily report scheduler started: {DAILY_REPORT_TIME}", flush=True)
    while True:
        current = dt.datetime.now()
        report_date = current.date().isoformat()
        if current.time() >= scheduled_time and last_sent_date != report_date:
            try:
                print(f"Daily report due: {report_date} {current.strftime('%H:%M:%S')}", flush=True)
                if not is_china_market_trading_day(report_date):
                    print(f"Daily report skipped: {report_date} is not a China market trading day", flush=True)
                    last_sent_date = report_date
                    time.sleep(30)
                    continue
                report = generate_watch_report()
                result = telegram_broadcast_report(report)
                print(
                    f"Daily report sent: {report_date} sent={len(result.get('sent', []))} failed={len(result.get('failed', []))}",
                    flush=True,
                )
                if result["failed"]:
                    print(f"Daily report partial failure: {result['failed']}", flush=True)
                last_sent_date = report_date
            except Exception as exc:
                print(f"Daily report error: {exc}", flush=True)
        time.sleep(30)


def position_report_loop():
    if not TELEGRAM_BOT_TOKEN or not POSITION_REPORT_TIME:
        print("Position report disabled: TELEGRAM_BOT_TOKEN or POSITION_REPORT_TIME is empty", flush=True)
        return
    if not telegram_owner_recipient_ids():
        print("Position report disabled: TELEGRAM_ALLOWED_USER_ID is empty", flush=True)
        return
    last_sent_date = ""
    try:
        scheduled_hour, scheduled_minute = [int(part) for part in POSITION_REPORT_TIME.split(":", 1)]
        scheduled_time = dt.time(scheduled_hour, scheduled_minute)
    except Exception:
        print(f"Position report disabled: invalid POSITION_REPORT_TIME={POSITION_REPORT_TIME}", flush=True)
        return
    current = dt.datetime.now()
    if current.time() >= scheduled_time and position_report_exists(current.date().isoformat()):
        last_sent_date = current.date().isoformat()
    print(f"Private position report scheduler started: {POSITION_REPORT_TIME}", flush=True)
    while True:
        current = dt.datetime.now()
        report_date = current.date().isoformat()
        if current.time() >= scheduled_time and last_sent_date != report_date:
            try:
                print(f"Position report due: {report_date} {current.strftime('%H:%M:%S')}", flush=True)
                if not is_china_market_trading_day(report_date):
                    print(f"Position report skipped: {report_date} is not a China market trading day", flush=True)
                    last_sent_date = report_date
                    time.sleep(30)
                    continue
                if not list_positions():
                    print("Position report skipped: no active positions", flush=True)
                    last_sent_date = report_date
                    time.sleep(30)
                    continue
                report = generate_position_report(report_date)
                result = telegram_send_owner_only(report)
                print(
                    f"Position report sent: {report_date} sent={len(result.get('sent', []))} failed={len(result.get('failed', []))}",
                    flush=True,
                )
                if result["failed"]:
                    print(f"Position report partial failure: {result['failed']}", flush=True)
                last_sent_date = report_date
            except Exception as exc:
                print(f"Position report error: {exc}", flush=True)
        time.sleep(30)


def telegram_loop():
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram disabled: TELEGRAM_BOT_TOKEN is empty", flush=True)
        return
    offset = None
    print("Telegram polling started", flush=True)
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
            print(f"Telegram polling error: {exc}", flush=True)
            time.sleep(5)


def main():
    init_db()
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=daily_report_loop, daemon=True).start()
    threading.Thread(target=position_report_loop, daemon=True).start()
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), AppHandler)
    print(f"Trade Review Assistant running at http://{APP_HOST}:{APP_PORT}", flush=True)
    print("Use APP_SECRET as the web password.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

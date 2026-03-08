import asyncio
import json
import logging
import os
import re
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import aiohttp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    import pymysql
except Exception:  # pragma: no cover
    pymysql = None


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DB_PATH = Path(os.getenv("APP_DB_PATH", str(BASE_DIR / "app.db")))
CONFIG_JSON_PATH = PROJECT_ROOT / "config.json"

APP_VERSION = "3.0.0"
WORKTOOL_API_BASE_DEFAULT = "https://api.worktool.ymdyes.cn"
DEFAULT_MESSAGE_API_URL = f"{WORKTOOL_API_BASE_DEFAULT}/wework/sendRawMessage"
ENABLE_TROUBLESHOOT = os.getenv("ENABLE_TROUBLESHOOT", "false").lower() in {"1", "true", "yes", "on"}


def now_iso() -> str:
    return datetime.now().isoformat()


def normalize_worktool_api_base(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return WORKTOOL_API_BASE_DEFAULT
    return raw.rstrip("/")


def normalize_public_base_url(value: str) -> str:
    raw = (value or "").strip()
    return raw.rstrip("/")


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}****{token[-4:]}"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS robots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            private_chat_enabled INTEGER NOT NULL DEFAULT 1,
            group_chat_enabled INTEGER NOT NULL DEFAULT 1,
            group_reply_only_when_mentioned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_token TEXT NOT NULL,
            model TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(name)
        );

        CREATE TABLE IF NOT EXISTS routing_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_id TEXT NOT NULL,
            scene TEXT NOT NULL CHECK(scene IN ('group', 'private')),
            pattern TEXT NOT NULL,
            provider_id INTEGER NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(robot_id) REFERENCES robots(robot_id) ON DELETE CASCADE,
            FOREIGN KEY(provider_id) REFERENCES ai_providers(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS default_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_id TEXT NOT NULL,
            scene TEXT NOT NULL CHECK(scene IN ('group', 'private')),
            reply_text TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(robot_id, scene),
            FOREIGN KEY(robot_id) REFERENCES robots(robot_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS message_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            robot_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
            scene TEXT NOT NULL CHECK(scene IN ('group', 'private')),
            group_name TEXT,
            sender_name TEXT,
            receiver_name TEXT,
            at_me INTEGER,
            text_type INTEGER,
            raw_content TEXT,
            normalized_content TEXT,
            provider_name TEXT,
            status TEXT NOT NULL CHECK(status IN ('received', 'success', 'skipped', 'failed')),
            error_message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(robot_id) REFERENCES robots(robot_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reply_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inbound_log_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('reply', 'skip')),
            reason_code TEXT NOT NULL,
            reason_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(inbound_log_id) REFERENCES message_logs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS operator_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            detail TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_rules_robot_scene ON routing_rules(robot_id, scene, priority);
        CREATE INDEX IF NOT EXISTS idx_logs_robot_time ON message_logs(robot_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_logs_direction_time ON message_logs(direction, created_at);
        """
    )

    robot_columns = {row["name"] for row in cur.execute("PRAGMA table_info(robots)").fetchall()}
    if "message_api_url" in robot_columns:
        robot_rows = cur.execute(
            """
            SELECT id, robot_id, name, private_chat_enabled, group_chat_enabled,
                   group_reply_only_when_mentioned, created_at, updated_at
            FROM robots
            ORDER BY id ASC
            """
        ).fetchall()
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS robots_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                private_chat_enabled INTEGER NOT NULL DEFAULT 1,
                group_chat_enabled INTEGER NOT NULL DEFAULT 1,
                group_reply_only_when_mentioned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        for row in robot_rows:
            cur.execute(
                """
                INSERT INTO robots_new(
                    id, robot_id, name, private_chat_enabled, group_chat_enabled,
                    group_reply_only_when_mentioned, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["robot_id"],
                    row["name"],
                    row["private_chat_enabled"],
                    row["group_chat_enabled"],
                    row["group_reply_only_when_mentioned"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        cur.execute("DROP TABLE robots")
        cur.execute("ALTER TABLE robots_new RENAME TO robots")
        cur.execute("PRAGMA foreign_keys = ON")

    fk_rows = cur.execute("PRAGMA foreign_key_list(ai_providers)").fetchall()
    provider_idx_rows = cur.execute("PRAGMA index_list(ai_providers)").fetchall()
    has_unique_name_index = False
    for idx in provider_idx_rows:
        if int(idx["unique"]) != 1:
            continue
        idx_cols = [x["name"] for x in cur.execute(f"PRAGMA index_info({idx['name']})").fetchall()]
        if idx_cols == ["name"]:
            has_unique_name_index = True
            break
    needs_provider_migration = len(fk_rows) > 0 or not has_unique_name_index
    if needs_provider_migration:
        rows = cur.execute("SELECT * FROM ai_providers ORDER BY id ASC").fetchall()
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_providers_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_token TEXT NOT NULL,
                model TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                provider_type TEXT NOT NULL DEFAULT 'openai',
                auth_scheme TEXT NOT NULL DEFAULT 'bearer',
                extra_json TEXT,
                UNIQUE(name)
            )
            """
        )
        used_names = set()
        for row in rows:
            provider_name = row["name"]
            if provider_name in used_names:
                provider_name = f"{provider_name}_{row['id']}"
            used_names.add(provider_name)
            cur.execute(
                """
                INSERT INTO ai_providers_new(
                    id, robot_id, name, base_url, api_token, model, enabled,
                    created_at, updated_at, provider_type, auth_scheme, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    "",
                    provider_name,
                    row["base_url"],
                    row["api_token"],
                    row["model"],
                    row["enabled"],
                    row["created_at"],
                    row["updated_at"],
                    row["provider_type"] if "provider_type" in row.keys() else "openai",
                    row["auth_scheme"] if "auth_scheme" in row.keys() else "bearer",
                    row["extra_json"] if "extra_json" in row.keys() else None,
                ),
            )
        cur.execute("DROP TABLE ai_providers")
        cur.execute("ALTER TABLE ai_providers_new RENAME TO ai_providers")
        cur.execute("PRAGMA foreign_keys = ON")
    provider_columns = {row["name"] for row in cur.execute("PRAGMA table_info(ai_providers)").fetchall()}
    if "provider_type" not in provider_columns:
        cur.execute("ALTER TABLE ai_providers ADD COLUMN provider_type TEXT NOT NULL DEFAULT 'openai'")
    if "auth_scheme" not in provider_columns:
        cur.execute("ALTER TABLE ai_providers ADD COLUMN auth_scheme TEXT NOT NULL DEFAULT 'bearer'")
    if "extra_json" not in provider_columns:
        cur.execute("ALTER TABLE ai_providers ADD COLUMN extra_json TEXT")

    settings = {
        "host": "0.0.0.0",
        "port": "8000",
        "log_level": "INFO",
        "auto_generate_chat_id": "true",
        "worktool_api_base": WORKTOOL_API_BASE_DEFAULT,
        "default_message_api_url": DEFAULT_MESSAGE_API_URL,
        "callback_public_base_url": "",
        "auto_bind_message_callback_on_create": "true",
    }
    for key, value in settings.items():
        cur.execute(
            """
            INSERT OR IGNORE INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, value, now_iso()),
        )

    row = cur.execute("SELECT value FROM app_settings WHERE key = 'worktool_api_base'").fetchone()
    worktool_base = normalize_worktool_api_base((row["value"] if row else WORKTOOL_API_BASE_DEFAULT))
    cur.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        ("worktool_api_base", worktool_base, now_iso()),
    )
    cur.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        ("default_message_api_url", f"{worktool_base}/wework/sendRawMessage", now_iso()),
    )

    conn.commit()
    conn.close()


def write_audit(action: str, target_type: str, target_id: str, detail: str) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO operator_audit_logs(action, target_type, target_id, detail, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (action, target_type, target_id, detail, now_iso()),
    )
    conn.commit()
    conn.close()


def import_config_json_if_needed() -> None:
    conn = get_conn()
    has_data = conn.execute("SELECT COUNT(1) AS c FROM robots").fetchone()["c"] > 0
    conn.close()
    if has_data or not CONFIG_JSON_PATH.exists():
        return

    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = get_conn()
    cur = conn.cursor()
    now = now_iso()
    robots = data.get("robots", {})

    for robot_id, cfg in robots.items():
        cur.execute(
            """
            INSERT INTO robots(
                robot_id, name, private_chat_enabled, group_chat_enabled,
                group_reply_only_when_mentioned, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                robot_id,
                cfg.get("name", robot_id),
                1 if cfg.get("private_chat_enabled", True) else 0,
                1 if cfg.get("group_chat_enabled", True) else 0,
                1 if cfg.get("group_reply_only_when_mentioned", False) else 0,
                now,
                now,
            ),
        )

        provider_ids: Dict[str, int] = {}
        for provider_name, provider_cfg in cfg.get("llm_apis", {}).items():
            cur.execute(
                """
                INSERT INTO ai_providers(
                    robot_id, name, base_url, api_token, model, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    robot_id,
                    provider_name,
                    provider_cfg.get("url", ""),
                    provider_cfg.get("token", ""),
                    provider_cfg.get("model"),
                    now,
                    now,
                ),
            )
            provider_ids[provider_name] = cur.lastrowid

        group_priority = 1
        for pattern, provider_name in cfg.get("group_llm_rules", {}).items():
            if provider_name not in provider_ids:
                continue
            cur.execute(
                """
                INSERT INTO routing_rules(
                    robot_id, scene, pattern, provider_id, priority, enabled, created_at, updated_at
                ) VALUES (?, 'group', ?, ?, ?, 1, ?, ?)
                """,
                (robot_id, pattern, provider_ids[provider_name], group_priority, now, now),
            )
            group_priority += 1

        private_priority = 1
        for pattern, provider_name in cfg.get("private_llm_rules", {}).items():
            if provider_name not in provider_ids:
                continue
            cur.execute(
                """
                INSERT INTO routing_rules(
                    robot_id, scene, pattern, provider_id, priority, enabled, created_at, updated_at
                ) VALUES (?, 'private', ?, ?, ?, 1, ?, ?)
                """,
                (robot_id, pattern, provider_ids[provider_name], private_priority, now, now),
            )
            private_priority += 1

        cur.execute(
            """
            INSERT INTO default_replies(robot_id, scene, reply_text, updated_at)
            VALUES (?, 'group', ?, ?)
            ON CONFLICT(robot_id, scene) DO UPDATE SET
                reply_text = excluded.reply_text,
                updated_at = excluded.updated_at
            """,
            (robot_id, cfg.get("group_default_reply"), now),
        )
        cur.execute(
            """
            INSERT INTO default_replies(robot_id, scene, reply_text, updated_at)
            VALUES (?, 'private', ?, ?)
            ON CONFLICT(robot_id, scene) DO UPDATE SET
                reply_text = excluded.reply_text,
                updated_at = excluded.updated_at
            """,
            (robot_id, cfg.get("private_default_reply"), now),
        )

    app_settings = data.get("app_settings", {})
    for key, value in app_settings.items():
        cur.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value).lower() if isinstance(value, bool) else str(value), now),
        )

    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    except sqlite3.OperationalError:
        return default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now_iso()),
    )
    conn.commit()
    conn.close()


def get_worktool_api_base() -> str:
    base = get_setting("worktool_api_base", WORKTOOL_API_BASE_DEFAULT)
    return normalize_worktool_api_base(base)


def get_default_message_api_url() -> str:
    return f"{get_worktool_api_base()}/wework/sendRawMessage"


def get_callback_public_base_url() -> str:
    return normalize_public_base_url(get_setting("callback_public_base_url", ""))


def build_robot_callback_url(robot_id: str) -> str:
    base = get_callback_public_base_url()
    if not base:
        return ""
    rid = (robot_id or "").strip()
    if not rid:
        return ""
    return f"{base}/api/v1/callback/qa/{rid}"


def _detect_local_ips() -> List[str]:
    ips: set[str] = set()
    try:
        _, _, host_ips = socket.gethostbyname_ex(socket.gethostname())
        for ip in host_ips:
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        if local_ip and not local_ip.startswith("127."):
            ips.add(local_ip)
    except Exception:
        pass
    return sorted(ips)


async def _detect_public_ip() -> str:
    timeout = aiohttp.ClientTimeout(total=2)
    providers = [
        ("https://api.ipify.org?format=json", "json"),
        ("https://ifconfig.me/ip", "text"),
    ]
    for url, mode in providers:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    if mode == "json":
                        data = await resp.json()
                        ip = str(data.get("ip") or "").strip()
                    else:
                        ip = (await resp.text()).strip()
                    if ip:
                        return ip
        except Exception:
            continue
    return ""


def parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def resolve_auth_scheme(
    provider_type: Literal["openai", "openclaw"],
    auth_scheme: Optional[Literal["bearer", "x-openclaw-token", "none"]] = None,
) -> Literal["bearer", "x-openclaw-token", "none"]:
    if auth_scheme is not None:
        return auth_scheme
    if provider_type == "openclaw":
        return "x-openclaw-token"
    return "bearer"


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    env: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _get_worktool_mysql_config() -> Optional[Dict[str, Any]]:
    if pymysql is None:
        return None
    env = {}
    env.update(_load_env_file(PROJECT_ROOT / "worktool_robot_check" / ".env"))
    env.update(_load_env_file(PROJECT_ROOT / ".env"))
    host = os.getenv("DB_HOST") or os.getenv("WORKTOOL_DB_HOST") or env.get("DB_HOST")
    port = os.getenv("DB_PORT") or os.getenv("WORKTOOL_DB_PORT") or env.get("DB_PORT") or "3306"
    user = os.getenv("DB_USER") or os.getenv("WORKTOOL_DB_USER") or env.get("DB_USER")
    password = os.getenv("DB_PASSWORD") or os.getenv("WORKTOOL_DB_PASSWORD") or env.get("DB_PASSWORD")
    database = os.getenv("DB_NAME") or os.getenv("WORKTOOL_DB_NAME") or env.get("DB_NAME")
    if not (host and user and database):
        return None
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password or "",
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 5,
        "read_timeout": 15,
        "write_timeout": 15,
    }


def _mysql_query(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    cfg = _get_worktool_mysql_config()
    if not cfg:
        return []
    try:
        conn = pymysql.connect(**cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall() or [])
        finally:
            conn.close()
    except Exception as e:
        logger.warning("mysql query failed: %s", e)
        return []


def parse_extra_json(extra_json: Optional[str]) -> Dict[str, Any]:
    if not extra_json:
        return {}
    try:
        data = json.loads(extra_json)
        if isinstance(data, dict):
            return data
        return {}
    except json.JSONDecodeError:
        return {}


def normalize_extra_json(extra_json: Optional[str]) -> Optional[str]:
    if extra_json is None:
        return None
    cleaned = extra_json.strip()
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"extra_json 不是有效JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError("extra_json 必须是JSON对象")
    return json.dumps(parsed, ensure_ascii=False)


class QARequest(BaseModel):
    spoken: str = ""
    rawSpoken: str = ""
    receivedName: str
    groupName: Optional[str] = None
    groupRemark: Optional[str] = None
    roomType: int
    atMe: bool
    textType: int


class QAResponse(BaseModel):
    code: int = 0
    message: str = "success"


class RobotCreate(BaseModel):
    robot_id: str
    name: str = "机器人"
    private_chat_enabled: bool = True
    group_chat_enabled: bool = True
    group_reply_only_when_mentioned: bool = False
    group_default_reply: Optional[str] = None
    private_default_reply: Optional[str] = None


class RobotUpdate(BaseModel):
    name: Optional[str] = None
    private_chat_enabled: Optional[bool] = None
    group_chat_enabled: Optional[bool] = None
    group_reply_only_when_mentioned: Optional[bool] = None
    group_default_reply: Optional[str] = None
    private_default_reply: Optional[str] = None


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_token: str
    model: Optional[str] = None
    provider_type: Literal["openai", "openclaw"] = "openai"
    auth_scheme: Optional[Literal["bearer", "x-openclaw-token", "none"]] = None
    extra_json: Optional[str] = None
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_token: Optional[str] = None
    model: Optional[str] = None
    provider_type: Optional[Literal["openai", "openclaw"]] = None
    auth_scheme: Optional[Literal["bearer", "x-openclaw-token", "none"]] = None
    extra_json: Optional[str] = None
    enabled: Optional[bool] = None


class OpenClawPushItem(BaseModel):
    type: int = 203
    receiver: str
    content: Optional[str] = None
    object_name: Optional[str] = None
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    extra_text: Optional[str] = None


class OpenClawPushPayload(BaseModel):
    provider_id: Optional[int] = None
    provider_name: Optional[str] = None
    list: List[OpenClawPushItem] = Field(default_factory=list)


class RuleCreate(BaseModel):
    robot_id: str
    scene: str
    pattern: str
    provider_id: int
    priority: int = 100
    enabled: bool = True


class RuleUpdate(BaseModel):
    pattern: Optional[str] = None
    provider_id: Optional[int] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class ReorderPayload(BaseModel):
    rule_ids: List[int] = Field(default_factory=list)


class MessageCallbackPayload(BaseModel):
    robot_id: str
    callback_url: str
    reply_all: int = 1


class RobotCallbackBindPayload(BaseModel):
    robot_id: str
    callback_url: str
    type: int


class RobotCallbackDeletePayload(BaseModel):
    robot_id: str
    type: int
    robot_key: str = ""


class CallbackTestPayload(BaseModel):
    callback_url: str


class WorkToolSettingsUpdate(BaseModel):
    worktool_api_base: Optional[str] = None
    callback_public_base_url: Optional[str] = None
    auto_bind_message_callback_on_create: Optional[bool] = None


class TroubleshootSearchPayload(BaseModel):
    robot_id: str = ""
    message_id: str = ""
    keyword: str = ""
    start_time: str = ""
    end_time: str = ""
    limit: int = 20


class MessageProcessor:
    def __init__(self) -> None:
        self.logger = logging.getLogger("message_processor")

    def _get_robot(self, robot_id: str) -> Optional[sqlite3.Row]:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT *
            FROM robots
            WHERE robot_id = ?
            """,
            (robot_id,),
        ).fetchone()
        conn.close()
        return row

    def _insert_message_log(
        self,
        robot_id: str,
        direction: str,
        scene: str,
        group_name: Optional[str],
        sender_name: Optional[str],
        receiver_name: Optional[str],
        at_me: Optional[bool],
        text_type: Optional[int],
        raw_content: str,
        normalized_content: str,
        provider_name: Optional[str],
        status: str,
        error_message: Optional[str] = None,
    ) -> int:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO message_logs(
                robot_id, direction, scene, group_name, sender_name, receiver_name,
                at_me, text_type, raw_content, normalized_content, provider_name,
                status, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                robot_id,
                direction,
                scene,
                group_name,
                sender_name,
                receiver_name,
                1 if at_me else 0 if at_me is not None else None,
                text_type,
                raw_content,
                normalized_content,
                provider_name,
                status,
                error_message,
                now_iso(),
            ),
        )
        inserted_id = cur.lastrowid
        conn.commit()
        conn.close()
        return inserted_id

    def _insert_decision(self, inbound_log_id: int, decision: str, code: str, text: str) -> None:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO reply_decisions(
                inbound_log_id, decision, reason_code, reason_text, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (inbound_log_id, decision, code, text, now_iso()),
        )
        conn.commit()
        conn.close()

    def _get_recent_group_messages(self, robot_id: str, group_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT sender_name, normalized_content, direction, created_at
            FROM message_logs
            WHERE robot_id = ? AND scene = 'group' AND group_name = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (robot_id, group_name, limit),
        ).fetchall()
        conn.close()
        items: List[Dict[str, Any]] = []
        for row in reversed(rows):
            items.append(
                {
                    "sender": row["sender_name"],
                    "content": row["normalized_content"],
                    "direction": row["direction"],
                    "time": row["created_at"],
                }
            )
        return items

    def _find_last_user_message(self, robot_id: str, group_name: str, sender_name: str) -> Optional[str]:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT normalized_content
            FROM message_logs
            WHERE robot_id = ? AND scene = 'group'
              AND direction = 'inbound' AND group_name = ? AND sender_name = ?
              AND COALESCE(normalized_content, '') <> ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (robot_id, group_name, sender_name),
        ).fetchone()
        conn.close()
        return row["normalized_content"] if row else None

    def _get_rules(self, robot_id: str, scene: str) -> List[sqlite3.Row]:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT
                r.*,
                p.name AS provider_name,
                p.base_url,
                p.api_token,
                p.model,
                p.enabled AS provider_enabled,
                p.provider_type,
                p.auth_scheme,
                p.extra_json
            FROM routing_rules r
            JOIN ai_providers p ON p.id = r.provider_id
            WHERE r.robot_id = ? AND r.scene = ? AND r.enabled = 1
            ORDER BY r.priority ASC, r.id ASC
            """,
            (robot_id, scene),
        ).fetchall()
        conn.close()
        return rows

    def _find_common_provider(self, robot_id: str) -> Optional[sqlite3.Row]:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT *
            FROM ai_providers
            WHERE name = 'common' AND enabled = 1
            ORDER BY id ASC
            LIMIT 1
            """,
        ).fetchone()
        conn.close()
        return row

    def _find_provider_for_scene(self, robot_id: str, scene: str, target_name: str) -> Optional[sqlite3.Row]:
        for rule in self._get_rules(robot_id, scene):
            try:
                if re.match(rule["pattern"], target_name):
                    return rule
            except re.error as e:
                self.logger.error("invalid regex pattern id=%s: %s", rule["id"], e)
        return None

    def _get_default_reply(self, robot_id: str, scene: str) -> Optional[str]:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT reply_text
            FROM default_replies
            WHERE robot_id = ? AND scene = ?
            LIMIT 1
            """,
            (robot_id, scene),
        ).fetchone()
        conn.close()
        if not row:
            return None
        val = row["reply_text"]
        if val is None:
            return None
        if isinstance(val, str) and not val.strip():
            return None
        return val

    def _build_auth_headers(self, auth_scheme: str, token: str) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if auth_scheme == "none":
            return headers
        if auth_scheme == "x-openclaw-token":
            headers["x-openclaw-token"] = token
            return headers
        headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _call_provider(
        self,
        provider_type: str,
        provider_name: str,
        base_url: str,
        token: str,
        model: Optional[str],
        auth_scheme: str,
        extra_json: Optional[str],
        robot_id: str,
        group_name: Optional[str],
        received_name: str,
        messages: List[Dict[str, str]],
    ) -> Optional[str]:
        provider_extra = parse_extra_json(extra_json)
        is_openrouter = "openrouter.ai" in (base_url or "").lower()
        auto_chat_id = parse_bool(get_setting("auto_generate_chat_id", "true"), True)
        send_chat_id = auto_chat_id and provider_type == "openai" and not is_openrouter
        if provider_type == "openclaw":
            send_chat_id = bool(provider_extra.get("send_chat_id", False))
        chat_id = None
        if send_chat_id:
            chat_id = f"wtbot:{robot_id}:{group_name or ''}:{received_name}"
        payload: Dict[str, Any] = {"messages": messages}
        request_model = provider_extra.get("model") or model
        if request_model:
            payload["model"] = request_model
        if chat_id:
            payload["chatId"] = chat_id
        if isinstance(provider_extra.get("request_body"), dict):
            payload.update(provider_extra["request_body"])
            payload["messages"] = messages

        headers = self._build_auth_headers(auth_scheme, token)
        if isinstance(provider_extra.get("request_headers"), dict):
            for key, value in provider_extra["request_headers"].items():
                if value is None:
                    continue
                headers[str(key)] = str(value)
        timeout = aiohttp.ClientTimeout(total=20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(base_url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        self.logger.error("provider=%s call failed, status=%s", provider_name, resp.status)
                        return None
                    data = await resp.json()
                    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            self.logger.error("provider=%s call exception: %s", provider_name, e)
            return None

    async def _send_raw_message(
        self, message_api_url: str, robot_id: str, message_list: List[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        url = f"{message_api_url}?robotId={robot_id}"
        payload = {"socketType": 2, "list": message_list}
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return True, ""
                    return False, f"message api status={resp.status}"
        except Exception as e:
            return False, str(e)

    async def _should_reply_group(
        self,
        robot_id: str,
        data: QARequest,
        robot_cfg: sqlite3.Row,
    ) -> Tuple[bool, str, str]:
        if data.atMe:
            return True, "AT_ME", "被@后强制回复"

        if robot_cfg["group_reply_only_when_mentioned"] == 1:
            return False, "NOT_MENTIONED", "未@机器人且配置要求仅@回复"

        provider = self._find_common_provider(robot_id)
        if not provider:
            return False, "COMMON_PROVIDER_MISSING", "缺少common provider用于群聊判定"

        context = self._get_recent_group_messages(robot_id, data.groupName or "", limit=5)
        prompt = (
            f"历史对话记录：{context}\n"
            f"你是一个机器人AI，请判断是否需要回复【{data.receivedName}】最后一句。\n"
            "若对机器人提问返回 A；若主要在和他人讨论返回 B。只输出一个字母。"
        )
        result = await self._call_provider(
            provider_type=provider["provider_type"],
            provider_name=provider["name"],
            base_url=provider["base_url"],
            token=provider["api_token"],
            model=provider["model"],
            auth_scheme=provider["auth_scheme"],
            extra_json=provider["extra_json"],
            robot_id=robot_id,
            group_name=data.groupName,
            received_name=data.receivedName,
            messages=[{"role": "user", "content": prompt}],
        )
        if result and "A" in result:
            return True, "COMMON_PROVIDER_REPLY", "common判定需要回复"
        return False, "COMMON_PROVIDER_SKIP", "common判定无需回复"

    async def process_message(self, robot_id: str, data: QARequest) -> None:
        robot_cfg = self._get_robot(robot_id)
        if not robot_cfg:
            self.logger.error("robot_id=%s not found", robot_id)
            return

        scene = "group" if (data.groupName and data.groupName != data.receivedName) else "private"
        raw_content = data.rawSpoken or data.spoken or ""
        normalized = data.spoken or ""

        inbound_log_id = self._insert_message_log(
            robot_id=robot_id,
            direction="inbound",
            scene=scene,
            group_name=data.groupName,
            sender_name=data.receivedName,
            receiver_name=robot_id,
            at_me=data.atMe,
            text_type=data.textType,
            raw_content=raw_content,
            normalized_content=normalized,
            provider_name=None,
            status="received",
        )

        if data.textType != 1:
            self._insert_decision(inbound_log_id, "skip", "NON_TEXT", "非文本消息不处理")
            return

        if scene == "group" and robot_cfg["group_chat_enabled"] == 0:
            self._insert_decision(inbound_log_id, "skip", "GROUP_DISABLED", "群聊已禁用")
            return

        if scene == "private" and robot_cfg["private_chat_enabled"] == 0:
            self._insert_decision(inbound_log_id, "skip", "PRIVATE_DISABLED", "私聊已禁用")
            return

        if scene == "group":
            should_reply, reason_code, reason_text = await self._should_reply_group(robot_id, data, robot_cfg)
            self._insert_decision(
                inbound_log_id,
                "reply" if should_reply else "skip",
                reason_code,
                reason_text,
            )
            if not should_reply:
                return

            content = normalized
            if not content.strip():
                content = self._find_last_user_message(robot_id, data.groupName or "", data.receivedName) or ""
            if not content.strip():
                content = "你好，请问有什么可以帮助你的吗？"
            target = data.groupName or data.receivedName
            provider_rule = self._find_provider_for_scene(robot_id, "group", data.groupName or "")
        else:
            self._insert_decision(inbound_log_id, "reply", "PRIVATE_DEFAULT", "私聊默认回复流程")
            content = normalized
            target = data.receivedName
            provider_rule = self._find_provider_for_scene(robot_id, "private", data.receivedName)

        reply_text: Optional[str] = None
        provider_name: Optional[str] = None
        if provider_rule:
            provider_name = provider_rule["provider_name"]
            reply_text = await self._call_provider(
                provider_type=provider_rule["provider_type"],
                provider_name=provider_rule["provider_name"],
                base_url=provider_rule["base_url"],
                token=provider_rule["api_token"],
                model=provider_rule["model"],
                auth_scheme=provider_rule["auth_scheme"],
                extra_json=provider_rule["extra_json"],
                robot_id=robot_id,
                group_name=data.groupName,
                received_name=data.receivedName,
                messages=[{"role": "user", "content": content}],
            )
            if reply_text:
                reply_text = reply_text.strip()

        if not reply_text:
            default = self._get_default_reply(robot_id, scene)
            if default:
                reply_text = default
                provider_name = provider_name or "default_reply"

        if not reply_text:
            self._insert_message_log(
                robot_id=robot_id,
                direction="outbound",
                scene=scene,
                group_name=data.groupName,
                sender_name=robot_id,
                receiver_name=target,
                at_me=None,
                text_type=1,
                raw_content="",
                normalized_content="",
                provider_name=provider_name,
                status="skipped",
                error_message="no rule matched and no default reply",
            )
            return

        ok, err = await self._send_raw_message(
            get_default_message_api_url(),
            robot_id,
            [{"type": 203, "titleList": [target], "receivedContent": reply_text}],
        )
        self._insert_message_log(
            robot_id=robot_id,
            direction="outbound",
            scene=scene,
            group_name=data.groupName,
            sender_name=robot_id,
            receiver_name=target,
            at_me=None,
            text_type=1,
            raw_content=reply_text,
            normalized_content=reply_text,
            provider_name=provider_name,
            status="success" if ok else "failed",
            error_message=err if not ok else None,
        )


app = FastAPI(title="WorkTool Bot Console API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("backend")
processor = MessageProcessor()


@app.on_event("startup")
async def startup() -> None:
    init_db()
    import_config_json_if_needed()
    level = get_setting("log_level", "INFO").upper()
    logging.getLogger().setLevel(getattr(logging, level, logging.INFO))
    logger.info("backend started, db=%s", DB_PATH)


@app.get("/api/v1/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "time": now_iso(),
        "enable_troubleshoot": ENABLE_TROUBLESHOOT,
    }


@app.get("/api/v1/settings/worktool")
async def get_worktool_settings() -> Dict[str, Any]:
    base = get_worktool_api_base()
    callback_public_base_url = get_callback_public_base_url()
    return {
        "worktool_api_base": base,
        "callback_public_base_url": callback_public_base_url,
        "auto_bind_message_callback_on_create": parse_bool(
            get_setting("auto_bind_message_callback_on_create", "true"),
            True,
        ),
        "message_send_api_url": f"{base}/wework/sendRawMessage",
        "callback_example_url": (
            f"{callback_public_base_url}/api/v1/callback/qa/{{robot_id}}"
            if callback_public_base_url
            else ""
        ),
    }


@app.put("/api/v1/settings/worktool")
async def update_worktool_settings(body: WorkToolSettingsUpdate) -> Dict[str, Any]:
    if body.worktool_api_base is not None:
        base = normalize_worktool_api_base(body.worktool_api_base)
        set_setting("worktool_api_base", base)
        set_setting("default_message_api_url", f"{base}/wework/sendRawMessage")
        write_audit("update", "app_settings", "worktool_api_base", base)
    else:
        base = get_worktool_api_base()

    if body.callback_public_base_url is not None:
        callback_public_base_url = normalize_public_base_url(body.callback_public_base_url)
        set_setting("callback_public_base_url", callback_public_base_url)
        write_audit("update", "app_settings", "callback_public_base_url", callback_public_base_url)
    else:
        callback_public_base_url = get_callback_public_base_url()

    if body.auto_bind_message_callback_on_create is not None:
        auto_bind = bool(body.auto_bind_message_callback_on_create)
        set_setting("auto_bind_message_callback_on_create", "true" if auto_bind else "false")
        write_audit("update", "app_settings", "auto_bind_message_callback_on_create", str(auto_bind))
    else:
        auto_bind = parse_bool(get_setting("auto_bind_message_callback_on_create", "true"), True)

    return {
        "ok": True,
        "worktool_api_base": base,
        "callback_public_base_url": callback_public_base_url,
        "auto_bind_message_callback_on_create": auto_bind,
        "message_send_api_url": f"{base}/wework/sendRawMessage",
        "callback_example_url": (
            f"{callback_public_base_url}/api/v1/callback/qa/{{robot_id}}"
            if callback_public_base_url
            else ""
        ),
    }


@app.get("/api/v1/settings/callback-base-suggestions")
async def get_callback_base_suggestions(request: Request) -> Dict[str, Any]:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").strip()
    host_header = (request.headers.get("host") or "").strip()
    scheme = forwarded_proto or request.url.scheme or "http"
    host = forwarded_host or host_header
    current_request_base = f"{scheme}://{host}" if host else ""

    host_without_port = host.split(":")[0] if host else ""
    host_port = ""
    if ":" in host:
        host_port = host.rsplit(":", 1)[1]
    if not host_port:
        host_port = "443" if scheme == "https" else "80"

    public_ip = await _detect_public_ip()
    public_base = f"{scheme}://{public_ip}:{host_port}" if public_ip else ""
    local_ips = _detect_local_ips()
    intranet_bases = [f"{scheme}://{ip}:{host_port}" for ip in local_ips]

    is_loopback = host_without_port in {"", "127.0.0.1", "localhost", "0.0.0.0"}
    suggested_base = ""
    if current_request_base and not is_loopback:
        suggested_base = current_request_base
    elif public_base:
        suggested_base = public_base
    elif intranet_bases:
        suggested_base = intranet_bases[0]

    return {
        "current_request_base": current_request_base,
        "public_base": public_base,
        "intranet_bases": intranet_bases,
        "suggested_base": suggested_base,
    }


@app.post("/api/v1/callback/qa/{robot_id}", response_model=QAResponse)
async def qa_callback(robot_id: str, req: QARequest) -> QAResponse:
    asyncio.create_task(processor.process_message(robot_id, req))
    return QAResponse(code=0, message="参数接收成功")


def _resolve_openclaw_provider(robot_id: str, payload: OpenClawPushPayload) -> sqlite3.Row:
    conn = get_conn()
    try:
        if payload.provider_id is not None:
            row = conn.execute(
                """
                SELECT *
                FROM ai_providers
                WHERE id = ? AND provider_type = 'openclaw' AND enabled = 1
                """,
                (payload.provider_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="provider_id 不存在或未启用")
            return row

        if payload.provider_name:
            row = conn.execute(
                """
                SELECT *
                FROM ai_providers
                WHERE name = ? AND provider_type = 'openclaw' AND enabled = 1
                LIMIT 1
                """,
                (payload.provider_name,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="provider_name 不存在或未启用")
            return row

        # 优先按该机器人的规则中实际引用的 openclaw provider 反查，避免要求 OpenClaw 侧配置 provider_name
        try:
            rule_rows = conn.execute(
                """
                SELECT DISTINCT p.*
                FROM rules r
                JOIN ai_providers p ON p.id = r.provider_id
                WHERE r.robot_id = ? AND r.enabled = 1 AND p.provider_type = 'openclaw' AND p.enabled = 1
                ORDER BY p.id ASC
                """,
                (robot_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # 兼容旧库：历史版本可能不存在 rules 表，回退到全局 provider 解析
            rule_rows = []
        if len(rule_rows) == 1:
            return rule_rows[0]
        if len(rule_rows) > 1:
            raise HTTPException(
                status_code=400,
                detail="该机器人关联了多个 openclaw provider，请在请求中指定 provider_id",
            )

        rows = conn.execute(
            """
            SELECT *
            FROM ai_providers
            WHERE provider_type = 'openclaw' AND enabled = 1
            ORDER BY id ASC
            """,
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="未配置启用中的 openclaw provider")
        if len(rows) > 1:
            raise HTTPException(status_code=400, detail="存在多个 openclaw provider，请指定 provider_id 或 provider_name")
        return rows[0]
    finally:
        conn.close()


def _build_worktool_message_list(items: List[OpenClawPushItem]) -> List[Dict[str, Any]]:
    if not items:
        raise HTTPException(status_code=400, detail="list 不能为空")
    result: List[Dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if item.type == 203:
            if not (item.content or "").strip():
                raise HTTPException(status_code=400, detail=f"第{idx}条消息缺少 content")
            result.append(
                {
                    "type": 203,
                    "titleList": [item.receiver],
                    "receivedContent": item.content.strip(),
                }
            )
        elif item.type == 218:
            if not item.object_name or not item.file_url:
                raise HTTPException(status_code=400, detail=f"第{idx}条文件消息缺少 object_name/file_url")
            result.append(
                {
                    "type": 218,
                    "titleList": [item.receiver],
                    "objectName": item.object_name,
                    "fileUrl": item.file_url,
                    "fileType": item.file_type or "image",
                    "extraText": item.extra_text or "",
                }
            )
        else:
            raise HTTPException(status_code=400, detail=f"第{idx}条消息 type={item.type} 暂不支持")
    return result


@app.post("/api/v1/openclaw/push/{robot_id}")
async def openclaw_push(robot_id: str, body: OpenClawPushPayload) -> Dict[str, Any]:
    provider = _resolve_openclaw_provider(robot_id, body)

    conn = get_conn()
    robot = conn.execute("SELECT * FROM robots WHERE robot_id = ?", (robot_id,)).fetchone()
    conn.close()
    if not robot:
        raise HTTPException(status_code=404, detail="robot not found")
    message_list = _build_worktool_message_list(body.list)
    ok, err = await processor._send_raw_message(get_default_message_api_url(), robot_id, message_list)
    if not ok:
        raise HTTPException(status_code=400, detail=f"下发失败：{err}")
    return {"ok": True, "provider_id": provider["id"], "sent_count": len(message_list)}


@app.get("/api/v1/dashboard/overview")
async def dashboard_overview() -> Dict[str, Any]:
    conn = get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    robots_total = conn.execute("SELECT COUNT(1) AS c FROM robots").fetchone()["c"]
    inbound_today = conn.execute(
        "SELECT COUNT(1) AS c FROM message_logs WHERE direction='inbound' AND date(created_at)=?",
        (today,),
    ).fetchone()["c"]
    outbound_success_today = conn.execute(
        "SELECT COUNT(1) AS c FROM message_logs WHERE direction='outbound' AND status='success' AND date(created_at)=?",
        (today,),
    ).fetchone()["c"]
    outbound_fail_today = conn.execute(
        "SELECT COUNT(1) AS c FROM message_logs WHERE direction='outbound' AND status='failed' AND date(created_at)=?",
        (today,),
    ).fetchone()["c"]
    conn.close()

    reply_rate = (outbound_success_today / inbound_today) if inbound_today else 0
    fail_rate = (outbound_fail_today / (outbound_success_today + outbound_fail_today)) if (outbound_success_today + outbound_fail_today) else 0
    return {
        "robots_total": robots_total,
        "inbound_today": inbound_today,
        "outbound_success_today": outbound_success_today,
        "outbound_fail_today": outbound_fail_today,
        "reply_rate": round(reply_rate, 4),
        "fail_rate": round(fail_rate, 4),
    }


@app.get("/api/v1/dashboard/trends")
async def dashboard_trends(days: int = Query(default=7, ge=1, le=90)) -> Dict[str, Any]:
    conn = get_conn()
    items: List[Dict[str, Any]] = []
    for i in range(days - 1, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        inbound = conn.execute(
            "SELECT COUNT(1) AS c FROM message_logs WHERE direction='inbound' AND date(created_at)=?",
            (d,),
        ).fetchone()["c"]
        outbound = conn.execute(
            "SELECT COUNT(1) AS c FROM message_logs WHERE direction='outbound' AND status='success' AND date(created_at)=?",
            (d,),
        ).fetchone()["c"]
        items.append({"date": d, "inbound": inbound, "outbound_success": outbound})
    conn.close()
    return {"days": days, "items": items}


@app.get("/api/v1/robots")
async def list_robots() -> Dict[str, Any]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT robot_id, name, private_chat_enabled, group_chat_enabled,
               group_reply_only_when_mentioned, created_at, updated_at
        FROM robots
        ORDER BY id ASC
        """
    ).fetchall()
    conn.close()
    return {
        "items": [
            {
                **dict(row),
                "private_chat_enabled": bool(row["private_chat_enabled"]),
                "group_chat_enabled": bool(row["group_chat_enabled"]),
                "group_reply_only_when_mentioned": bool(row["group_reply_only_when_mentioned"]),
            }
            for row in rows
        ]
    }


@app.get("/api/v1/robots/{robot_id}")
async def get_robot(robot_id: str) -> Dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM robots WHERE robot_id = ?", (robot_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="robot not found")
    defaults = conn.execute(
        "SELECT scene, reply_text FROM default_replies WHERE robot_id = ?",
        (robot_id,),
    ).fetchall()
    conn.close()
    payload = dict(row)
    payload["private_chat_enabled"] = bool(row["private_chat_enabled"])
    payload["group_chat_enabled"] = bool(row["group_chat_enabled"])
    payload["group_reply_only_when_mentioned"] = bool(row["group_reply_only_when_mentioned"])
    payload["defaults"] = {d["scene"]: d["reply_text"] for d in defaults}
    return payload


@app.post("/api/v1/robots")
async def create_robot(body: RobotCreate) -> Dict[str, Any]:
    conn = get_conn()
    now = now_iso()
    try:
        conn.execute(
            """
            INSERT INTO robots(
                robot_id, name, private_chat_enabled, group_chat_enabled,
                group_reply_only_when_mentioned, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.robot_id,
                (body.name or "机器人").strip() or "机器人",
                1 if body.private_chat_enabled else 0,
                1 if body.group_chat_enabled else 0,
                1 if body.group_reply_only_when_mentioned else 0,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO default_replies(robot_id, scene, reply_text, updated_at)
            VALUES (?, 'group', ?, ?)
            """,
            (body.robot_id, body.group_default_reply, now),
        )
        conn.execute(
            """
            INSERT INTO default_replies(robot_id, scene, reply_text, updated_at)
            VALUES (?, 'private', ?, ?)
            """,
            (body.robot_id, body.private_default_reply, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"create robot failed: {e}") from e
    conn.close()

    auto_bind = parse_bool(get_setting("auto_bind_message_callback_on_create", "true"), True)
    callback_url = build_robot_callback_url(body.robot_id)
    if auto_bind:
        if not callback_url:
            rollback_conn = get_conn()
            rollback_conn.execute("DELETE FROM robots WHERE robot_id = ?", (body.robot_id,))
            rollback_conn.commit()
            rollback_conn.close()
            raise HTTPException(
                status_code=400,
                detail="create robot failed: 已回滚。自动绑定消息回调失败：未配置“回调公网基础地址”。",
            )
        try:
            await bind_message_callback(body.robot_id, callback_url, 1)
        except HTTPException as e:
            rollback_conn = get_conn()
            rollback_conn.execute("DELETE FROM robots WHERE robot_id = ?", (body.robot_id,))
            rollback_conn.commit()
            rollback_conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"create robot failed: 已回滚。自动绑定消息回调失败：{e.detail}",
            ) from e

    write_audit("create", "robot", body.robot_id, "create robot")
    return {
        "ok": True,
        "auto_bind_message_callback": auto_bind,
        "callback_url": callback_url if auto_bind else "",
    }


@app.put("/api/v1/robots/{robot_id}")
async def update_robot(robot_id: str, body: RobotUpdate) -> Dict[str, Any]:
    updates = []
    params: List[Any] = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.private_chat_enabled is not None:
        updates.append("private_chat_enabled = ?")
        params.append(1 if body.private_chat_enabled else 0)
    if body.group_chat_enabled is not None:
        updates.append("group_chat_enabled = ?")
        params.append(1 if body.group_chat_enabled else 0)
    if body.group_reply_only_when_mentioned is not None:
        updates.append("group_reply_only_when_mentioned = ?")
        params.append(1 if body.group_reply_only_when_mentioned else 0)

    conn = get_conn()
    if updates:
        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(robot_id)
        conn.execute(f"UPDATE robots SET {', '.join(updates)} WHERE robot_id = ?", params)

    if body.group_default_reply is not None:
        conn.execute(
            """
            INSERT INTO default_replies(robot_id, scene, reply_text, updated_at)
            VALUES (?, 'group', ?, ?)
            ON CONFLICT(robot_id, scene) DO UPDATE SET reply_text=excluded.reply_text, updated_at=excluded.updated_at
            """,
            (robot_id, body.group_default_reply, now_iso()),
        )
    if body.private_default_reply is not None:
        conn.execute(
            """
            INSERT INTO default_replies(robot_id, scene, reply_text, updated_at)
            VALUES (?, 'private', ?, ?)
            ON CONFLICT(robot_id, scene) DO UPDATE SET reply_text=excluded.reply_text, updated_at=excluded.updated_at
            """,
            (robot_id, body.private_default_reply, now_iso()),
        )
    conn.commit()
    conn.close()
    write_audit("update", "robot", robot_id, "update robot")
    return {"ok": True}


@app.delete("/api/v1/robots/{robot_id}")
async def delete_robot(robot_id: str) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM robots WHERE robot_id = ?", (robot_id,))
    conn.commit()
    conn.close()
    write_audit("delete", "robot", robot_id, "delete robot")
    return {"ok": True}


@app.get("/api/v1/providers")
async def list_providers(robot_id: Optional[str] = None) -> Dict[str, Any]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM ai_providers ORDER BY id ASC").fetchall()
    conn.close()
    items = []
    for row in rows:
        d = dict(row)
        d["enabled"] = bool(row["enabled"])
        d["api_token_masked"] = mask_token(row["api_token"])
        d.pop("api_token")
        d.pop("robot_id", None)
        items.append(d)
    return {"items": items}


@app.post("/api/v1/providers")
async def create_provider(body: ProviderCreate) -> Dict[str, Any]:
    try:
        normalized_extra_json = normalize_extra_json(body.extra_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    conn = get_conn()
    try:
        auth_scheme = resolve_auth_scheme(body.provider_type, body.auth_scheme)
        exists = conn.execute("SELECT id FROM ai_providers WHERE name = ? LIMIT 1", (body.name,)).fetchone()
        if exists:
            raise HTTPException(status_code=400, detail="create provider failed: 名称已存在，请使用不同名称")
        conn.execute(
            """
            INSERT INTO ai_providers(
                robot_id, name, base_url, api_token, model, provider_type, auth_scheme, extra_json,
                enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "",
                body.name,
                body.base_url,
                body.api_token,
                body.model,
                body.provider_type,
                auth_scheme,
                normalized_extra_json,
                1 if body.enabled else 0,
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
    except HTTPException:
        conn.close()
        raise
    except sqlite3.IntegrityError as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"create provider failed: {e}") from e
    conn.close()
    write_audit("create", "provider", body.name, "global provider")
    return {"ok": True}


@app.put("/api/v1/providers/{provider_id}")
async def update_provider(provider_id: int, body: ProviderUpdate) -> Dict[str, Any]:
    updates = []
    params: List[Any] = []
    if body.name is not None:
        conn = get_conn()
        dup = conn.execute(
            "SELECT id FROM ai_providers WHERE name = ? AND id <> ? LIMIT 1",
            (body.name, provider_id),
        ).fetchone()
        conn.close()
        if dup:
            raise HTTPException(status_code=400, detail="update provider failed: 名称已存在，请使用不同名称")
        updates.append("name = ?")
        params.append(body.name)
    if body.base_url is not None:
        updates.append("base_url = ?")
        params.append(body.base_url)
    if body.api_token is not None:
        updates.append("api_token = ?")
        params.append(body.api_token)
    if body.model is not None:
        updates.append("model = ?")
        params.append(body.model)
    current_provider_type = body.provider_type
    if body.provider_type is None and body.auth_scheme is None:
        conn = get_conn()
        row = conn.execute("SELECT provider_type FROM ai_providers WHERE id = ?", (provider_id,)).fetchone()
        conn.close()
        if row:
            current_provider_type = row["provider_type"]
    if body.provider_type is not None:
        updates.append("provider_type = ?")
        params.append(body.provider_type)
    if body.auth_scheme is not None:
        updates.append("auth_scheme = ?")
        params.append(body.auth_scheme)
    elif current_provider_type is not None:
        updates.append("auth_scheme = ?")
        params.append(resolve_auth_scheme(current_provider_type))
    if body.extra_json is not None:
        try:
            normalized_extra_json = normalize_extra_json(body.extra_json)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        updates.append("extra_json = ?")
        params.append(normalized_extra_json)
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if body.enabled else 0)
    if not updates:
        return {"ok": True}

    updates.append("updated_at = ?")
    params.append(now_iso())
    params.append(provider_id)
    conn = get_conn()
    conn.execute(f"UPDATE ai_providers SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    write_audit("update", "provider", str(provider_id), "update provider")
    return {"ok": True}


@app.delete("/api/v1/providers/{provider_id}")
async def delete_provider(provider_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))
    conn.commit()
    conn.close()
    write_audit("delete", "provider", str(provider_id), "delete provider")
    return {"ok": True}


@app.get("/api/v1/robots/{robot_id}/rules")
async def list_rules(robot_id: str, scene: Optional[str] = None) -> Dict[str, Any]:
    sql = """
        SELECT r.*, p.name AS provider_name
        FROM routing_rules r
        JOIN ai_providers p ON p.id = r.provider_id
        WHERE r.robot_id = ?
    """
    params: List[Any] = [robot_id]
    if scene:
        sql += " AND r.scene = ?"
        params.append(scene)
    sql += " ORDER BY r.scene ASC, r.priority ASC, r.id ASC"
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return {"items": [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]}


@app.post("/api/v1/rules")
async def create_rule(body: RuleCreate) -> Dict[str, Any]:
    if body.scene not in {"group", "private"}:
        raise HTTPException(status_code=400, detail="scene must be group/private")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO routing_rules(
            robot_id, scene, pattern, provider_id, priority, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            body.robot_id,
            body.scene,
            body.pattern,
            body.provider_id,
            body.priority,
            1 if body.enabled else 0,
            now_iso(),
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()
    write_audit("create", "rule", body.robot_id, body.pattern)
    return {"ok": True}


@app.put("/api/v1/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleUpdate) -> Dict[str, Any]:
    updates = []
    params: List[Any] = []
    if body.pattern is not None:
        updates.append("pattern = ?")
        params.append(body.pattern)
    if body.provider_id is not None:
        updates.append("provider_id = ?")
        params.append(body.provider_id)
    if body.priority is not None:
        updates.append("priority = ?")
        params.append(body.priority)
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if body.enabled else 0)
    if not updates:
        return {"ok": True}
    updates.append("updated_at = ?")
    params.append(now_iso())
    params.append(rule_id)
    conn = get_conn()
    conn.execute(f"UPDATE routing_rules SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    write_audit("update", "rule", str(rule_id), "update rule")
    return {"ok": True}


@app.delete("/api/v1/rules/{rule_id}")
async def delete_rule(rule_id: int) -> Dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM routing_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    write_audit("delete", "rule", str(rule_id), "delete rule")
    return {"ok": True}


@app.put("/api/v1/robots/{robot_id}/rules/reorder")
async def reorder_rules(robot_id: str, scene: str, body: ReorderPayload) -> Dict[str, Any]:
    if scene not in {"group", "private"}:
        raise HTTPException(status_code=400, detail="scene must be group/private")
    conn = get_conn()
    for idx, rule_id in enumerate(body.rule_ids, start=1):
        conn.execute(
            """
            UPDATE routing_rules
            SET priority = ?, updated_at = ?
            WHERE id = ? AND robot_id = ? AND scene = ?
            """,
            (idx, now_iso(), rule_id, robot_id, scene),
        )
    conn.commit()
    conn.close()
    write_audit("reorder", "rule", robot_id, f"scene={scene}, count={len(body.rule_ids)}")
    return {"ok": True}


@app.get("/api/v1/logs/messages")
async def list_message_logs(
    robot_id: Optional[str] = None,
    scene: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> Dict[str, Any]:
    where = []
    params: List[Any] = []
    if robot_id:
        where.append("robot_id = ?")
        params.append(robot_id)
    if scene:
        where.append("scene = ?")
        params.append(scene)
    if status:
        where.append("status = ?")
        params.append(status)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(1) AS c FROM message_logs {where_clause}", params).fetchone()["c"]
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT *
        FROM message_logs
        {where_clause}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    ).fetchall()
    conn.close()
    return {"total": total, "page": page, "page_size": page_size, "items": [dict(r) for r in rows]}


@app.get("/api/v1/logs/messages/{log_id}")
async def get_message_log(log_id: int) -> Dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM message_logs WHERE id = ?", (log_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="log not found")
    decision = conn.execute(
        "SELECT * FROM reply_decisions WHERE inbound_log_id = ? ORDER BY id DESC LIMIT 1",
        (log_id,),
    ).fetchone()
    conn.close()
    return {"log": dict(row), "decision": dict(decision) if decision else None}


@app.get("/api/v1/logs/decisions")
async def list_decisions(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=200)) -> Dict[str, Any]:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(1) AS c FROM reply_decisions").fetchone()["c"]
    rows = conn.execute(
        """
        SELECT d.*, m.robot_id, m.group_name, m.sender_name, m.normalized_content
        FROM reply_decisions d
        JOIN message_logs m ON m.id = d.inbound_log_id
        ORDER BY d.id DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, (page - 1) * page_size),
    ).fetchall()
    conn.close()
    return {"total": total, "page": page, "page_size": page_size, "items": [dict(r) for r in rows]}


async def fetch_worktool_robot_info(path: str, robot_id: str) -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=20)
    url = f"{get_worktool_api_base()}{path}"
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params={"robotId": robot_id}) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail=f"worktool api status={resp.status}")
                data = await resp.json()
                if data.get("code") != 200:
                    raise HTTPException(status_code=400, detail=data.get("message", "worktool api failed"))
                return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"worktool api error: {e}") from e


async def fetch_worktool_api(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=20)
    url = f"{get_worktool_api_base()}{path}"
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail=f"worktool api status={resp.status}")
                data = await resp.json()
                if data.get("code") != 200:
                    raise HTTPException(status_code=400, detail=data.get("message", "worktool api failed"))
                return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"worktool api error: {e}") from e


async def fetch_worktool_api_loose(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=20)
    url = f"{get_worktool_api_base()}{path}"
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail=f"worktool api status={resp.status}")
                data = await resp.json()
                code = data.get("code")
                if code not in (0, 200):
                    raise HTTPException(status_code=400, detail=data.get("message", "worktool api failed"))
                return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"worktool api error: {e}") from e


async def test_message_callback_url(callback_url: str) -> Dict[str, Any]:
    test_body = {
        "spoken": "您好,欢迎使用WorkTool~",
        "rawSpoken": "@小明 您好,欢迎使用WorkTool~",
        "receivedName": "WorkTool",
        "groupName": "WorkTool",
        "groupRemark": "WorkTool",
        "roomType": 1,
        "atMe": True,
        "textType": 1,
    }
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(callback_url, json=test_body) as resp:
                body_text = await resp.text()
                if resp.status != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"测试失败：响应状态码必须为200，当前为{resp.status}",
                    )

                content_type = (resp.headers.get("Content-Type") or "").lower()
                if "application/json" not in content_type:
                    raise HTTPException(
                        status_code=400,
                        detail=f"测试失败：响应头Content-Type必须包含application/json，当前为{content_type or '空'}",
                    )

                try:
                    response_json = json.loads(body_text)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail="测试失败：响应内容不是有效JSON格式")

                data = response_json.get("data")
                if data is not None and not isinstance(data, dict):
                    raise HTTPException(status_code=400, detail='测试失败："data" 键错误，请删除或改为对象')

                return {
                    "ok": True,
                    "message": "回调地址测试通过",
                    "status_code": resp.status,
                    "content_type": content_type,
                    "response_json": response_json,
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"测试失败：请求发生错误：{e}") from e


async def bind_message_callback(robot_id: str, callback_url: str, reply_all: int = 1) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/update"
    payload = {
        "openCallback": 1,
        "replyAll": 1 if reply_all else 0,
        "callbackUrl": callback_url,
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params={"robotId": robot_id}, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"绑定失败：平台接口HTTP状态码为{resp.status}",
                    )
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail="绑定失败：平台返回不是JSON")
                code = data.get("code")
                if code not in (0, 200):
                    raise HTTPException(
                        status_code=400,
                        detail=f"绑定失败：{data.get('message', '未知错误')} (code={code})",
                    )
                return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"绑定失败：请求异常：{e}") from e


async def bind_robot_callback_type(robot_id: str, callback_url: str, callback_type: int) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/callBack/bind"
    payload = {"type": callback_type, "callBackUrl": callback_url}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params={"robotId": robot_id}, json=payload) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"绑定失败：type={callback_type} 平台接口HTTP状态码为{resp.status}",
                    )
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail=f"绑定失败：type={callback_type} 平台返回不是JSON")
                code = data.get("code")
                if code not in (0, 200):
                    raise HTTPException(
                        status_code=400,
                        detail=f"绑定失败：type={callback_type} {data.get('message', '未知错误')} (code={code})",
                    )
                return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"绑定失败：type={callback_type} 请求异常：{e}") from e


async def test_callback_url_status_2xx(callback_url: str) -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=12)
    payload = {"event": "worktool_callback_test", "timestamp": now_iso()}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(callback_url, json=payload) as resp:
                if 200 <= resp.status < 300:
                    return {"ok": True, "status_code": resp.status}
                text = await resp.text()
                raise HTTPException(
                    status_code=400,
                    detail=f"测试失败：响应状态码不是2xx，当前为{resp.status}，响应内容：{text[:300]}",
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"测试失败：请求异常：{e}") from e


async def delete_robot_callback_type(robot_id: str, callback_type: int, robot_key: str = "") -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/callBack/deleteByType"
    payload = {"type": callback_type}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                params={"robotId": robot_id, "robotKey": robot_key},
                json=payload,
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise HTTPException(
                        status_code=400,
                        detail=f"删除失败：type={callback_type} 平台接口HTTP状态码为{resp.status}",
                    )
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=400, detail=f"删除失败：type={callback_type} 平台返回不是JSON")
                code = data.get("code")
                if code not in (0, 200):
                    raise HTTPException(
                        status_code=400,
                        detail=f"删除失败：type={callback_type} {data.get('message', '未知错误')} (code={code})",
                    )
                return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"删除失败：type={callback_type} 请求异常：{e}") from e


@app.get("/api/v1/robot-info/detail")
async def get_robot_info_detail(robot_id: str) -> Dict[str, Any]:
    return await fetch_worktool_robot_info("/robot/robotInfo/get-detail", robot_id)


@app.get("/api/v1/robot-info/callbacks")
async def get_robot_info_callbacks(robot_id: str) -> Dict[str, Any]:
    return await fetch_worktool_robot_info("/robot/robotInfo/callBack/get", robot_id)


@app.get("/api/v1/robot-info/online")
async def get_robot_info_online(robot_id: str) -> Dict[str, Any]:
    return await fetch_worktool_robot_info("/robot/robotInfo/online", robot_id)


@app.get("/api/v1/robot-info/online-infos")
async def get_robot_info_online_infos(robot_id: str) -> Dict[str, Any]:
    return await fetch_worktool_robot_info("/robot/robotInfo/onlineInfos", robot_id)


@app.post("/api/v1/robot-info/message-callback/test")
async def test_robot_message_callback(body: MessageCallbackPayload) -> Dict[str, Any]:
    callback_url = (body.callback_url or "").strip()
    if not callback_url:
        raise HTTPException(status_code=400, detail="测试失败：回调地址不能为空")
    test_result = await test_message_callback_url(callback_url)
    return {"code": 200, "message": "测试通过", "data": test_result}


@app.post("/api/v1/robot-info/callbacks/test")
async def test_robot_callback(body: CallbackTestPayload) -> Dict[str, Any]:
    callback_url = (body.callback_url or "").strip()
    if not callback_url:
        raise HTTPException(status_code=400, detail="测试失败：回调地址不能为空")
    result = await test_callback_url_status_2xx(callback_url)
    return {"code": 200, "message": "测试通过", "data": result}


@app.post("/api/v1/robot-info/message-callback/bind")
async def bind_robot_message_callback(body: MessageCallbackPayload) -> Dict[str, Any]:
    callback_url = (body.callback_url or "").strip()
    robot_id = (body.robot_id or "").strip()
    if not robot_id:
        raise HTTPException(status_code=400, detail="绑定失败：robot_id不能为空")
    if not callback_url:
        raise HTTPException(status_code=400, detail="绑定失败：回调地址不能为空")

    test_result = await test_message_callback_url(callback_url)
    bind_result = await bind_message_callback(robot_id, callback_url, body.reply_all)
    write_audit("bind", "message_callback", robot_id, callback_url)
    return {
        "code": 200,
        "message": "绑定成功",
        "data": {"test_result": test_result, "bind_result": bind_result},
    }


@app.post("/api/v1/robot-info/callbacks/bind")
async def bind_robot_callback(body: RobotCallbackBindPayload) -> Dict[str, Any]:
    robot_id = (body.robot_id or "").strip()
    callback_url = (body.callback_url or "").strip()
    callback_type = body.type
    if not robot_id:
        raise HTTPException(status_code=400, detail="绑定失败：robot_id不能为空")
    if not callback_url:
        raise HTTPException(status_code=400, detail="绑定失败：回调地址不能为空")
    if callback_type not in {0, 1, 5, 6}:
        raise HTTPException(status_code=400, detail="绑定失败：type仅支持0/1/5/6")

    bind_result = await bind_robot_callback_type(robot_id, callback_url, callback_type)
    write_audit("bind", "callback_type", robot_id, f"type={callback_type}, url={callback_url}")
    return {"code": 200, "message": "绑定成功", "data": bind_result}


@app.post("/api/v1/robot-info/callbacks/delete-by-type")
async def delete_robot_callback(body: RobotCallbackDeletePayload) -> Dict[str, Any]:
    robot_id = (body.robot_id or "").strip()
    callback_type = body.type
    robot_key = body.robot_key or ""
    if not robot_id:
        raise HTTPException(status_code=400, detail="删除失败：robot_id不能为空")
    if callback_type not in {0, 1, 5, 6, 11}:
        raise HTTPException(status_code=400, detail="删除失败：type仅支持0/1/5/6/11")

    delete_result = await delete_robot_callback_type(robot_id, callback_type, robot_key)
    write_audit("delete", "callback_type", robot_id, f"type={callback_type}")
    return {"code": 200, "message": "删除成功", "data": delete_result}


@app.get("/api/v1/worktool/qa-logs")
async def get_worktool_qa_logs(
    robot_id: str,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    sort: str = Query(default="start_time,desc"),
    name: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"robotId": robot_id, "page": page, "size": size, "sort": sort}
    if name:
        params["name"] = name
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    return await fetch_worktool_api("/robot/qaLog/list", params)


def _mask_ip(ip: Optional[str]) -> str:
    if not ip:
        return "-"
    chunks = ip.split(".")
    if len(chunks) == 4:
        return f"{chunks[0]}.{chunks[1]}.*.*"
    return ip


def _safe_limit(limit: int, default: int = 20, max_limit: int = 100) -> int:
    if limit <= 0:
        return default
    return min(limit, max_limit)


def _parse_loose_datetime(value: Optional[str]) -> float:
    if not value:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0


async def _fetch_qa_logs_page(
    robot_id: str,
    page: int,
    size: int,
    keyword: str = "",
    start_time: str = "",
    end_time: str = "",
    message_id: str = "",
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"robotId": robot_id, "page": page, "size": size, "sort": "start_time,desc"}
    if keyword:
        params["name"] = keyword
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    if message_id:
        params["messageId"] = message_id
        params["message_id"] = message_id
    data = await fetch_worktool_api("/robot/qaLog/list", params)
    return data.get("data", {}).get("list", []) or []


def _mysql_resolve_robot_id_by_message(message_id: str) -> Optional[str]:
    if not message_id:
        return None
    sqls = [
        (
            """
            SELECT robot_id
            FROM raw_message_record
            WHERE message_id = %s AND robot_id IS NOT NULL AND robot_id <> ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (message_id,),
        ),
        (
            """
            SELECT robot_id
            FROM raw_msg_confirm
            WHERE message_id = %s AND robot_id IS NOT NULL AND robot_id <> ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (message_id,),
        ),
        (
            """
            SELECT robot_id
            FROM qa_log
            WHERE message_id = %s AND robot_id IS NOT NULL AND robot_id <> ''
            ORDER BY id DESC
            LIMIT 1
            """,
            (message_id,),
        ),
    ]
    for sql, params in sqls:
        rows = _mysql_query(sql, params)
        if rows and rows[0].get("robot_id"):
            return str(rows[0]["robot_id"])
    return None


def _mysql_build_where(
    robot_id: str,
    message_id: str,
    keyword: str,
    start_time: str,
    end_time: str,
    robot_field: Optional[str] = None,
    message_field: Optional[str] = None,
    time_field: Optional[str] = None,
    keyword_fields: Optional[List[str]] = None,
) -> Tuple[str, List[Any]]:
    where: List[str] = []
    params: List[Any] = []
    if robot_id and robot_field:
        where.append(f"{robot_field} = %s")
        params.append(robot_id)
    if message_id and message_field:
        where.append(f"{message_field} = %s")
        params.append(message_id)
    if start_time and time_field:
        where.append(f"{time_field} >= %s")
        params.append(start_time)
    if end_time and time_field:
        where.append(f"{time_field} <= %s")
        params.append(end_time)
    if keyword and keyword_fields:
        chunks = []
        for f in keyword_fields:
            chunks.append(f"{f} LIKE %s")
            params.append(f"%{keyword}%")
        if chunks:
            where.append("(" + " OR ".join(chunks) + ")")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def _mysql_table_query(
    table: str,
    robot_id: str,
    message_id: str,
    keyword: str,
    start_time: str,
    end_time: str,
    limit: int,
    robot_field: Optional[str] = None,
    message_field: Optional[str] = None,
    time_field: Optional[str] = None,
    keyword_fields: Optional[List[str]] = None,
    order_by: str = "id DESC",
) -> List[Dict[str, Any]]:
    where_sql, params = _mysql_build_where(
        robot_id=robot_id,
        message_id=message_id,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        robot_field=robot_field,
        message_field=message_field,
        time_field=time_field,
        keyword_fields=keyword_fields,
    )
    sql = f"SELECT * FROM {table}{where_sql} ORDER BY {order_by} LIMIT %s"
    params.append(limit)
    return _mysql_query(sql, tuple(params))


def _sanitize_mysql_raw_message_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "时间": x.get("create_time"),
            "消息ID": x.get("message_id") or "",
            "消息类型": x.get("type_list"),
            "来源IP": _mask_ip(x.get("ip")),
            "是否API发送": x.get("api_send"),
            "发送内容": (x.get("body") or "")[:300],
        }
        for x in rows
    ]


def _sanitize_mysql_raw_confirm_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def parse_json_list(value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if not value:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return []

    def is_success(row: Dict[str, Any]) -> bool:
        error_code = row.get("error_code")
        if error_code is not None:
            try:
                return int(error_code) == 0
            except Exception:
                pass

        fail_list = parse_json_list(row.get("fail_list"))
        if len(fail_list) > 0:
            return False

        success_list = parse_json_list(row.get("success_list"))
        if len(success_list) > 0:
            return True

        if (row.get("error_reason") or "").strip():
            return False

        # 与用户环境保持一致：raw_success=0 代表执行成功
        if row.get("raw_success") is not None:
            try:
                return int(row.get("raw_success")) == 0
            except Exception:
                return False
        return False

    return [
        {
            "时间": x.get("create_time"),
            "消息ID": x.get("message_id") or "",
            "执行结果": "成功" if is_success(x) else "失败",
            "耗时(秒)": x.get("time_cost"),
            "失败原因": (x.get("error_reason") or "")[:200],
        }
        for x in rows
    ]


async def _resolve_robot_id_by_message(message_id: str) -> Optional[str]:
    conn = get_conn()
    robots = conn.execute("SELECT robot_id FROM robots ORDER BY id ASC").fetchall()
    conn.close()
    page_size = 50
    max_scan_pages = 2

    async def has_msg_in_raw_message(robot_id: str, use_filter: bool) -> bool:
        for page in range(1, max_scan_pages + 1):
            params: Dict[str, Any] = {"robotId": robot_id, "page": page, "size": page_size}
            if use_filter:
                params["messageId"] = message_id
            data = await fetch_worktool_api_loose("/wework/listRawMessage", params)
            payload = data.get("data", {})
            rows = payload.get("list") or payload.get("records") or payload or []
            if not isinstance(rows, list):
                rows = []
            if any((x.get("messageId") or "") == message_id for x in rows):
                return True
            total_page = int(payload.get("totalPage") or 0) if isinstance(payload, dict) else 0
            if use_filter and len(rows) == 0:
                return False
            if total_page > 0 and page >= total_page:
                return False
            if not rows:
                return False
        return False

    async def has_msg_in_raw_confirm(robot_id: str, use_filter: bool) -> bool:
        for page in range(1, max_scan_pages + 1):
            params: Dict[str, Any] = {"robotId": robot_id, "page": page, "size": page_size}
            if use_filter:
                params["messageId"] = message_id
            data = await fetch_worktool_api_loose("/robot/rawMsg/list", params)
            payload = data.get("data", {})
            if isinstance(payload, dict):
                rows = payload.get("list") or payload.get("records") or []
                total_page = int(payload.get("totalPage") or 0)
            elif isinstance(payload, list):
                rows = payload
                total_page = 0
            else:
                rows = []
                total_page = 0
            if any((x.get("messageId") or "") == message_id for x in rows):
                return True
            if use_filter and len(rows) == 0:
                return False
            if total_page > 0 and page >= total_page:
                return False
            if not rows:
                return False
        return False

    for row in robots:
        robot_id = row["robot_id"]
        try:
            if await has_msg_in_raw_message(robot_id, use_filter=True):
                return robot_id
            if await has_msg_in_raw_confirm(robot_id, use_filter=True):
                return robot_id
            rows = await _fetch_qa_logs_page(robot_id=robot_id, page=1, size=50, message_id=message_id)
            if any((x.get("messageId") or "") == message_id for x in rows):
                return robot_id
        except HTTPException:
            continue
    return None


def _sanitize_qa_rows(rows: List[Dict[str, Any]], message_id: str, limit: int) -> List[Dict[str, Any]]:
    filtered = rows
    if message_id:
        filtered = [x for x in rows if (x.get("messageId") or "") == message_id]
    output = []
    for x in filtered[:limit]:
        output.append(
            {
                "时间": x.get("startTime"),
                "提问者": x.get("receivedName"),
                "会话": x.get("groupName"),
                "消息类型": x.get("textType"),
                "是否@机器人": x.get("atMe"),
                "问题": x.get("question") or x.get("rawSpoken"),
                "回答": x.get("answer") or "",
                "回调耗时(秒)": x.get("timeCost"),
                "消息ID": x.get("messageId") or "",
            }
        )
    return output


async def _fetch_raw_message_records(robot_id: str, message_id: str, limit: int) -> List[Dict[str, Any]]:
    # 官方历史接口（对应 raw_message_record）
    # 文档: /wework/listRawMessage
    params = {"robotId": robot_id, "page": 1, "size": min(max(limit, 20), 200)}
    data = await fetch_worktool_api_loose("/wework/listRawMessage", params)
    raw_list = data.get("data", {}).get("list") or data.get("data", {}).get("records") or data.get("data") or []
    if not isinstance(raw_list, list):
        return []
    if message_id:
        raw_list = [x for x in raw_list if (x.get("messageId") or "") == message_id]
    return [
        {
            "时间": x.get("createTime") or x.get("startTime") or "-",
            "消息ID": x.get("messageId") or "",
            "接收对象": x.get("titleList") or x.get("title") or "",
            "发送内容": x.get("receivedContent") or x.get("rawMsg") or "",
            "消息类型": x.get("type"),
            "状态": x.get("status"),
        }
        for x in raw_list[:limit]
    ]


async def _fetch_raw_msg_confirms(robot_id: str, message_id: str, limit: int) -> List[Dict[str, Any]]:
    # 官方历史接口（对应 raw_msg_confirm）
    # 文档: /robot/rawMsg/list
    params = {"robotId": robot_id, "size": min(max(limit, 20), 200)}
    data = await fetch_worktool_api_loose("/robot/rawMsg/list", params)
    raw_list = data.get("data") or []
    if not isinstance(raw_list, list):
        return []
    if message_id:
        raw_list = [x for x in raw_list if (x.get("messageId") or "") == message_id]
    return [
        {
            "时间": x.get("createTime") or "-",
            "消息ID": x.get("messageId") or "",
            "执行结果": "成功" if x.get("success") else "失败",
            "执行耗时(秒)": x.get("costTimes"),
            "失败原因": x.get("errorReason") or "",
        }
        for x in raw_list[:limit]
    ]


@app.post("/api/v1/troubleshoot/search")
async def troubleshoot_search(body: TroubleshootSearchPayload) -> Dict[str, Any]:
    if not ENABLE_TROUBLESHOOT:
        raise HTTPException(status_code=404, detail="机器人排查功能在开源版中默认关闭")

    robot_id = (body.robot_id or "").strip()
    message_id = (body.message_id or "").strip()
    keyword = (body.keyword or "").strip()
    start_time = (body.start_time or "").strip()
    end_time = (body.end_time or "").strip()
    limit = _safe_limit(body.limit)

    if not robot_id and not message_id:
        raise HTTPException(status_code=400, detail="robot_id 和 message_id 至少填写一个")

    resolved_from_message = False
    if not robot_id and message_id:
        robot_id = _mysql_resolve_robot_id_by_message(message_id) or ""
        if not robot_id:
            robot_id = await _resolve_robot_id_by_message(message_id) or ""
        resolved_from_message = bool(robot_id)

    if not robot_id:
        conn = get_conn()
        robot_count = conn.execute("SELECT COUNT(1) AS c FROM robots").fetchone()["c"]
        conn.close()
        return {
            "input": {
                "robot_id": body.robot_id,
                "message_id": message_id,
                "keyword": keyword,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
            "resolved": {
                "robot_id": "",
                "message_resolved_robot": False,
            },
            "sections": {
                "机器人状态": {},
                "回调配置": [],
                "上线记录(最多20条)": [],
                "raw_message_record 指令发送记录表": [],
                "raw_msg_confirm 指令客户端执行结果表": [],
                "问答回调记录": [],
                "本地消息处理记录": [],
            },
            "diagnostics": [
                f"未通过 message_id 反查到机器人（已扫描当前系统内 {robot_count} 个机器人）。",
                "请补充 robot_id 后重试，可直接定位该消息所在机器人。",
            ],
        }

    detail_data = (await fetch_worktool_robot_info("/robot/robotInfo/get-detail", robot_id)).get("data", {}) or {}
    callbacks_data = (await fetch_worktool_robot_info("/robot/robotInfo/callBack/get", robot_id)).get("data", []) or []
    online_data = (await fetch_worktool_robot_info("/robot/robotInfo/online", robot_id)).get("data", False)
    online_infos_data = (await fetch_worktool_robot_info("/robot/robotInfo/onlineInfos", robot_id)).get("data", []) or []
    online_infos_data = sorted(
        online_infos_data,
        key=lambda x: _parse_loose_datetime(x.get("onlineTime")),
        reverse=True,
    )[:20]
    qa_rows = await _fetch_qa_logs_page(
        robot_id=robot_id,
        page=1,
        size=max(limit, 50),
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        message_id=message_id,
    )
    mysql_raw_message_rows = _mysql_table_query(
        table="raw_message_record",
        robot_id=robot_id,
        message_id=message_id,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        robot_field="robot_id",
        message_field="message_id",
        time_field="create_time",
        keyword_fields=["body", "ip", "type_list"],
    )
    mysql_raw_confirm_rows = _mysql_table_query(
        table="raw_msg_confirm",
        robot_id=robot_id,
        message_id=message_id,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        robot_field="robot_id",
        message_field="message_id",
        time_field="create_time",
        keyword_fields=["raw_msg", "error_reason", "success_list", "fail_list"],
    )
    raw_message_rows = (
        _sanitize_mysql_raw_message_rows(mysql_raw_message_rows)
        if mysql_raw_message_rows
        else await _fetch_raw_message_records(robot_id, message_id, limit)
    )
    raw_confirm_rows = (
        _sanitize_mysql_raw_confirm_rows(mysql_raw_confirm_rows)
        if mysql_raw_confirm_rows
        else await _fetch_raw_msg_confirms(robot_id, message_id, limit)
    )

    conn = get_conn()
    logs = conn.execute(
        """
        SELECT direction, scene, group_name, sender_name, receiver_name, normalized_content, status, created_at
        FROM message_logs
        WHERE robot_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (robot_id, limit),
    ).fetchall()
    conn.close()

    diagnostics: List[str] = []
    if message_id and not any((x.get("messageId") or "") == message_id for x in qa_rows):
        diagnostics.append("该 message_id 在近1页问答记录中未命中，请检查时间范围后重试。")
    if not online_data:
        diagnostics.append("机器人当前离线。")
    if detail_data.get("robotType") == 4:
        diagnostics.append("机器人状态无效（robotType=4）。")
    if message_id and len(raw_message_rows) > 0 and len(raw_confirm_rows) == 0:
        diagnostics.append("找到指令发送记录，但未找到客户端执行结果记录。")

    return {
        "input": {
            "robot_id": body.robot_id,
            "message_id": message_id,
            "keyword": keyword,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        },
        "resolved": {
            "robot_id": robot_id,
            "message_resolved_robot": resolved_from_message,
        },
        "sections": {
            "机器人状态": {
                "机器人ID": detail_data.get("robotId") or robot_id,
                "机器人名称": detail_data.get("name") or "-",
                "识别账号昵称": detail_data.get("showName") or "-",
                "企业名称": detail_data.get("corporation") or "-",
                "首次登录": detail_data.get("firstLogin") or "-",
                "授权到期": detail_data.get("authExpir") or detail_data.get("authExpire") or "-",
                "当前在线": bool(online_data),
                "是否有效": bool(detail_data.get("robotType") != 4),
            },
            "回调配置": [
                {"回调类型": x.get("typeName"), "回调地址": x.get("callBackUrl"), "类型编号": x.get("type")}
                for x in callbacks_data[:20]
            ],
            "上线记录(最多20条)": [
                {
                    "上线时间": x.get("onlineTime"),
                    "下线时间": x.get("offline") or "",
                    "在线时长(分钟)": x.get("onlineTimes"),
                    "登录IP": _mask_ip(x.get("ip")),
                }
                for x in online_infos_data
            ],
            "raw_message_record 指令发送记录表": raw_message_rows,
            "raw_msg_confirm 指令客户端执行结果表": raw_confirm_rows,
            "问答回调记录": _sanitize_qa_rows(qa_rows, message_id, limit),
            "本地消息处理记录": [
                {
                    "时间": r["created_at"],
                    "方向": "接收" if r["direction"] == "inbound" else "发送",
                    "场景": "群聊" if r["scene"] == "group" else "私聊",
                    "会话": r["group_name"] or r["receiver_name"] or "-",
                    "消息": (r["normalized_content"] or "")[:120],
                    "状态": r["status"],
                }
                for r in logs
            ],
        },
        "diagnostics": diagnostics,
    }


if __name__ == "__main__":
    import uvicorn

    init_db()
    import_config_json_if_needed()
    host = get_setting("host", "0.0.0.0")
    port = int(get_setting("port", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)

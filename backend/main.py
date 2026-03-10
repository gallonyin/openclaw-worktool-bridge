import hashlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import aiohttp
import jwt
import pymysql
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from worktool_troubleshoot import TroubleshootSearchPayload, run_troubleshoot_search

APP_VERSION = "4.0.0"
WORKTOOL_API_BASE_DEFAULT = "https://api.worktool.ymdyes.cn"
DEFAULT_MESSAGE_API_URL = f"{WORKTOOL_API_BASE_DEFAULT}/wework/sendRawMessage"

AUTH_PBKDF2_ITERATIONS = int(os.getenv("AUTH_PBKDF2_ITERATIONS", "390000"))
AUTH_JWT_SECRET = os.getenv("AUTH_JWT_SECRET", "").strip()
AUTH_JWT_EXPIRE_DAYS = int(os.getenv("AUTH_JWT_EXPIRE_DAYS", "30"))
AUTH_SMS_ENABLED = os.getenv("AUTH_SMS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_TROUBLESHOOT = os.getenv("ENABLE_TROUBLESHOOT", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_RUNTIME_WORKTOOL_SETTINGS = os.getenv("ENABLE_RUNTIME_WORKTOOL_SETTINGS", "false").strip().lower() in {"1", "true", "yes", "on"}
WORKTOOL_API_BASE_FIXED_RAW = os.getenv("WORKTOOL_API_BASE", "").strip()
CALLBACK_PUBLIC_BASE_URL_FIXED_RAW = os.getenv("CALLBACK_PUBLIC_BASE_URL", "").strip()
ADMIN_PHONE_WHITELIST = {
    x.strip()
    for x in os.getenv("ADMIN_PHONE_WHITELIST", "").split(",")
    if x.strip()
}

SMS_HUARUI_API_URL = os.getenv("SMS_HUARUI_API_URL", "").strip()
SMS_HUARUI_APPKEY = os.getenv("SMS_HUARUI_APPKEY", "").strip()
SMS_HUARUI_APPSECRET = os.getenv("SMS_HUARUI_APPSECRET", "").strip()
SMS_HUARUI_SIGN = os.getenv("SMS_HUARUI_SIGN", "【南京亚美达科技】").strip()
SMS_CODE_EXPIRE_MINUTES = int(os.getenv("SMS_CODE_EXPIRE_MINUTES", "15"))


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


def now_iso() -> str:
    return datetime.now().isoformat()


def normalize_worktool_api_base(value: str) -> str:
    raw = (value or "").strip()
    return raw.rstrip("/") if raw else WORKTOOL_API_BASE_DEFAULT


def normalize_public_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}****{token[-4:]}"


def _db_cfg() -> Dict[str, Any]:
    host = os.getenv("APP_MYSQL_HOST", "").strip()
    port = int(os.getenv("APP_MYSQL_PORT", "3306").strip())
    user = os.getenv("APP_MYSQL_USER", "").strip()
    password = os.getenv("APP_MYSQL_PASSWORD", "")
    database = os.getenv("APP_MYSQL_DATABASE", "").strip()
    if not (host and user and database):
        raise HTTPException(status_code=503, detail="auth mysql not configured")
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
        "connect_timeout": 5,
        "read_timeout": 15,
        "write_timeout": 15,
    }


def db_conn() -> Any:
    return pymysql.connect(**_db_cfg())


def init_db() -> None:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  phone VARCHAR(20) NOT NULL UNIQUE,
                  password_hash VARCHAR(255) NOT NULL,
                  company_name VARCHAR(128) NULL,
                  token_version INT NOT NULL DEFAULT 0,
                  is_active TINYINT(1) NOT NULL DEFAULT 1,
                  last_login_at DATETIME NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SHOW COLUMNS FROM users LIKE 'last_login_at'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sms_codes (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  phone VARCHAR(20) NOT NULL,
                  scene ENUM('register','reset_password','login') NOT NULL,
                  code_hash VARCHAR(255) NOT NULL,
                  expire_at DATETIME NOT NULL,
                  used_at DATETIME NULL,
                  request_ip VARCHAR(64) NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX idx_sms_phone_scene_created (phone, scene, created_at),
                  INDEX idx_sms_expire (expire_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS robots (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  robot_id VARCHAR(128) NOT NULL UNIQUE,
                  name VARCHAR(255) NOT NULL,
                  private_chat_enabled TINYINT(1) NOT NULL DEFAULT 1,
                  group_chat_enabled TINYINT(1) NOT NULL DEFAULT 1,
                  group_reply_only_when_mentioned TINYINT(1) NOT NULL DEFAULT 0,
                  version INT NOT NULL DEFAULT 0,
                  created_by BIGINT NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  CONSTRAINT fk_robots_created_by FOREIGN KEY (created_by) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_robots (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  user_id BIGINT NOT NULL,
                  robot_pk BIGINT NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE KEY uk_user_robot (user_id, robot_pk),
                  INDEX idx_user_robots_user (user_id),
                  INDEX idx_user_robots_robot (robot_pk),
                  CONSTRAINT fk_user_robots_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                  CONSTRAINT fk_user_robots_robot FOREIGN KEY (robot_pk) REFERENCES robots(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_providers (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  created_by BIGINT NOT NULL,
                  name VARCHAR(128) NOT NULL UNIQUE,
                  base_url VARCHAR(512) NOT NULL,
                  api_token TEXT NOT NULL,
                  model VARCHAR(128) NULL,
                  provider_type ENUM('openai','openclaw') NOT NULL DEFAULT 'openai',
                  auth_scheme ENUM('bearer','x-openclaw-token','none') NOT NULL DEFAULT 'bearer',
                  extra_json JSON NULL,
                  enabled TINYINT(1) NOT NULL DEFAULT 1,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX idx_provider_created_by (created_by),
                  CONSTRAINT fk_provider_created_by FOREIGN KEY (created_by) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS routing_rules (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  robot_pk BIGINT NOT NULL,
                  scene ENUM('group','private') NOT NULL,
                  pattern VARCHAR(1024) NOT NULL,
                  provider_id BIGINT NOT NULL,
                  priority INT NOT NULL DEFAULT 100,
                  enabled TINYINT(1) NOT NULL DEFAULT 1,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX idx_rules_robot_scene_priority (robot_pk, scene, priority),
                  CONSTRAINT fk_rules_robot FOREIGN KEY (robot_pk) REFERENCES robots(id) ON DELETE CASCADE,
                  CONSTRAINT fk_rules_provider FOREIGN KEY (provider_id) REFERENCES ai_providers(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SHOW COLUMNS FROM ai_providers LIKE 'robot_pk'")
            if cur.fetchone():
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ai_providers_new (
                      id BIGINT PRIMARY KEY AUTO_INCREMENT,
                      created_by BIGINT NOT NULL,
                      name VARCHAR(128) NOT NULL UNIQUE,
                      base_url VARCHAR(512) NOT NULL,
                      api_token TEXT NOT NULL,
                      model VARCHAR(128) NULL,
                      provider_type ENUM('openai','openclaw') NOT NULL DEFAULT 'openai',
                      auth_scheme ENUM('bearer','x-openclaw-token','none') NOT NULL DEFAULT 'bearer',
                      extra_json JSON NULL,
                      enabled TINYINT(1) NOT NULL DEFAULT 1,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                      INDEX idx_provider_created_by (created_by),
                      CONSTRAINT fk_provider_created_by FOREIGN KEY (created_by) REFERENCES users(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cur.execute(
                    """
                    INSERT INTO ai_providers_new(id,created_by,name,base_url,api_token,model,provider_type,auth_scheme,extra_json,enabled,created_at,updated_at)
                    SELECT id,
                           COALESCE((SELECT MIN(id) FROM users), 1) AS created_by,
                           CASE WHEN cnt > 1 THEN CONCAT(name, '_', id) ELSE name END AS name,
                           base_url,api_token,model,provider_type,auth_scheme,extra_json,enabled,created_at,updated_at
                    FROM (
                      SELECT p.*,
                             COUNT(*) OVER(PARTITION BY p.name) AS cnt
                      FROM ai_providers p
                    ) t
                    ORDER BY id ASC
                    """
                )
                try:
                    cur.execute("ALTER TABLE routing_rules DROP FOREIGN KEY fk_rules_provider")
                except Exception:
                    pass
                cur.execute("DROP TABLE ai_providers")
                cur.execute("RENAME TABLE ai_providers_new TO ai_providers")
                try:
                    cur.execute(
                        """
                        ALTER TABLE routing_rules
                        ADD CONSTRAINT fk_rules_provider
                        FOREIGN KEY (provider_id) REFERENCES ai_providers(id) ON DELETE CASCADE
                        """
                    )
                except Exception:
                    pass
            cur.execute("SHOW COLUMNS FROM ai_providers LIKE 'created_by'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE ai_providers ADD COLUMN created_by BIGINT NULL")
                cur.execute(
                    """
                    UPDATE ai_providers p
                    LEFT JOIN (
                      SELECT r.provider_id, MIN(ur.user_id) AS user_id
                      FROM routing_rules r
                      JOIN user_robots ur ON ur.robot_pk=r.robot_pk
                      GROUP BY r.provider_id
                    ) t ON t.provider_id=p.id
                    SET p.created_by=COALESCE(t.user_id, (SELECT MIN(id) FROM users))
                    WHERE p.created_by IS NULL
                    """
                )
                cur.execute("ALTER TABLE ai_providers MODIFY COLUMN created_by BIGINT NOT NULL")
            try:
                cur.execute("ALTER TABLE ai_providers ADD INDEX idx_provider_created_by (created_by)")
            except Exception:
                pass
            try:
                cur.execute(
                    """
                    ALTER TABLE ai_providers
                    ADD CONSTRAINT fk_provider_created_by FOREIGN KEY (created_by) REFERENCES users(id)
                    """
                )
            except Exception:
                pass
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS default_replies (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  robot_pk BIGINT NOT NULL,
                  scene ENUM('group','private') NOT NULL,
                  reply_text TEXT NULL,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uk_default_reply_robot_scene (robot_pk, scene),
                  CONSTRAINT fk_default_replies_robot FOREIGN KEY (robot_pk) REFERENCES robots(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                  `key` VARCHAR(64) PRIMARY KEY,
                  `value` TEXT NOT NULL,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS app_sms_record (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  account VARCHAR(32) NOT NULL,
                  source VARCHAR(64) NOT NULL,
                  source_ip VARCHAR(64) NOT NULL,
                  phone VARCHAR(16) NOT NULL,
                  sign VARCHAR(32) DEFAULT NULL,
                  content VARCHAR(512) NOT NULL,
                  send_time DATETIME NOT NULL,
                  msgid VARCHAR(64) NOT NULL,
                  result VARCHAR(1024) NOT NULL,
                  KEY idx_sms_record_phone (phone),
                  KEY idx_sms_record_send_time (send_time),
                  KEY idx_sms_record_source (source)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS message_logs (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  robot_pk BIGINT NOT NULL,
                  direction ENUM('inbound','outbound') NOT NULL,
                  scene ENUM('group','private') NOT NULL,
                  normalized_content TEXT,
                  status ENUM('received','success','skipped','failed') NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX idx_logs_robot_time (robot_pk, created_at),
                  CONSTRAINT fk_logs_robot FOREIGN KEY (robot_pk) REFERENCES robots(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

            cur.execute("SELECT 1 FROM app_settings WHERE `key`='worktool_api_base' LIMIT 1")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO app_settings(`key`,`value`) VALUES('worktool_api_base', %s)",
                    (WORKTOOL_API_BASE_DEFAULT,),
                )
            cur.execute("SELECT 1 FROM app_settings WHERE `key`='auto_bind_message_callback_on_create' LIMIT 1")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO app_settings(`key`,`value`) VALUES('auto_bind_message_callback_on_create','true')"
                )
            cur.execute("SELECT 1 FROM app_settings WHERE `key`='callback_public_base_url' LIMIT 1")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO app_settings(`key`,`value`) VALUES('callback_public_base_url','')"
                )
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `value` FROM app_settings WHERE `key`=%s LIMIT 1", (key,))
            row = cur.fetchone()
            return str(row["value"]) if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_settings(`key`,`value`) VALUES(%s,%s)
                ON DUPLICATE KEY UPDATE `value`=VALUES(`value`), updated_at=CURRENT_TIMESTAMP
                """,
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


def _is_valid_phone(phone: str) -> bool:
    p = (phone or "").strip()
    return bool(re.fullmatch(r"1\d{10}", p)) and not p.startswith("170")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, AUTH_PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${AUTH_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_str, salt_hex, digest_hex = (stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def _hash_sms_code(code: str) -> str:
    pepper = AUTH_JWT_SECRET or "dev-pepper"
    digest = hashlib.sha256(f"{code}|{pepper}".encode("utf-8")).hexdigest()
    return f"sha256${digest}"


def _verify_sms_code(code: str, stored: str) -> bool:
    if not stored or not stored.startswith("sha256$"):
        return False
    expected = stored.split("$", 1)[1]
    actual = _hash_sms_code(code).split("$", 1)[1]
    return secrets.compare_digest(actual, expected)


def _consume_sms_code(cur: Any, phone: str, scene: str, code: str) -> bool:
    cur.execute(
        """
        SELECT id,code_hash FROM sms_codes
        WHERE phone=%s AND scene=%s AND used_at IS NULL AND expire_at > UTC_TIMESTAMP()
        ORDER BY id DESC LIMIT 20
        """,
        (phone, scene),
    )
    rows = cur.fetchall() or []
    for row in rows:
        if _verify_sms_code(code, str(row["code_hash"])):
            cur.execute("UPDATE sms_codes SET used_at=UTC_TIMESTAMP() WHERE id=%s", (row["id"],))
            return True
    return False


def _create_access_token(user_id: int, token_version: int) -> str:
    if not AUTH_JWT_SECRET:
        raise HTTPException(status_code=503, detail="jwt secret not configured")
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "token_version": int(token_version),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=AUTH_JWT_EXPIRE_DAYS)).timestamp()),
    }
    return jwt.encode(payload, AUTH_JWT_SECRET, algorithm="HS256")


def _parse_bearer_token(authorization: Optional[str]) -> str:
    raw = (authorization or "").strip()
    if not raw.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = raw[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


def _decode_access_token(token: str) -> Dict[str, Any]:
    if not AUTH_JWT_SECRET:
        raise HTTPException(status_code=503, detail="jwt secret not configured")
    try:
        return jwt.decode(token, AUTH_JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="token expired") from e
    except Exception as e:
        raise HTTPException(status_code=401, detail="invalid token") from e


def _is_admin_phone(phone: str) -> bool:
    p = (phone or "").strip()
    return bool(p) and p in ADMIN_PHONE_WHITELIST


def _require_admin(user: Dict[str, Any]) -> None:
    if not _is_admin_phone(str(user.get("phone") or "")):
        raise HTTPException(status_code=403, detail="仅管理员可访问")


def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    token = _parse_bearer_token(authorization)
    payload = _decode_access_token(token)
    try:
        user_id = int(payload.get("sub", 0))
        token_version = int(payload.get("token_version", -1))
    except Exception as e:
        raise HTTPException(status_code=401, detail="invalid token payload") from e
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="invalid token subject")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,phone,company_name,token_version,is_active,last_login_at,created_at,updated_at FROM users WHERE id=%s LIMIT 1",
                (user_id,),
            )
            user = cur.fetchone()
        if not user or int(user["is_active"]) != 1:
            raise HTTPException(status_code=401, detail="user not active")
        if int(user["token_version"]) != token_version:
            raise HTTPException(status_code=401, detail="token revoked")
        return user
    finally:
        conn.close()


def _sms_signature(timestamp_ms: int) -> str:
    raw = f"{SMS_HUARUI_APPKEY}{SMS_HUARUI_APPSECRET}{timestamp_ms}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def _send_sms_via_huarui(phone: str, content: str) -> Dict[str, Any]:
    if not SMS_HUARUI_API_URL or not SMS_HUARUI_APPKEY or not SMS_HUARUI_APPSECRET:
        raise HTTPException(status_code=503, detail="sms provider not configured")
    ts_ms = int(datetime.utcnow().timestamp() * 1000)
    body = {
        "appkey": SMS_HUARUI_APPKEY,
        "appsecret": SMS_HUARUI_APPSECRET,
        "appcode": "1000",
        "timestamp": ts_ms,
        "sign": _sms_signature(ts_ms),
        "phone": phone,
        "msg": content,
    }
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(SMS_HUARUI_API_URL, json=body) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"sms upstream status={resp.status}")
            if not isinstance(data, dict):
                raise HTTPException(status_code=502, detail="sms upstream invalid response")
            return data


# ----- models -----
class QARequest(BaseModel):
    spoken: str = ""
    rawSpoken: str = ""
    receivedName: str = ""
    groupName: Optional[str] = None
    roomType: int = 0
    atMe: bool = False
    textType: int = 1


class QAResponse(BaseModel):
    code: int = 0
    message: str = "success"


class SmsSendRequest(BaseModel):
    phone: str
    scene: Literal["register", "reset_password", "login"] = "register"


class AuthRegisterRequest(BaseModel):
    phone: str
    sms_code: Optional[str] = None
    password: str
    company_name: Optional[str] = None


class AuthLoginRequest(BaseModel):
    phone: str
    password: str


class AuthResetPasswordRequest(BaseModel):
    phone: str
    sms_code: Optional[str] = None
    new_password: str


class WorkToolSettingsUpdate(BaseModel):
    worktool_api_base: Optional[str] = None
    callback_public_base_url: Optional[str] = None
    auto_bind_message_callback_on_create: Optional[bool] = None


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


class RuleCreate(BaseModel):
    robot_id: str
    scene: Literal["group", "private"]
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


# ----- permission helpers -----
def _bound_robot_pk_set(user_id: int) -> set:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT robot_pk FROM user_robots WHERE user_id=%s", (user_id,))
            return {int(x["robot_pk"]) for x in (cur.fetchall() or [])}
    finally:
        conn.close()


def _get_robot_by_id_or_404(robot_id: str) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM robots WHERE robot_id=%s LIMIT 1", (robot_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="robot not found")
            return row
    finally:
        conn.close()


def _require_robot_access(user_id: int, robot_id: str) -> Dict[str, Any]:
    row = _get_robot_by_id_or_404(robot_id)
    if int(row["id"]) not in _bound_robot_pk_set(user_id):
        raise HTTPException(status_code=403, detail="无权访问该机器人")
    return row


def _provider_exists(provider_id: int) -> bool:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM ai_providers WHERE id=%s LIMIT 1", (provider_id,))
            return bool(cur.fetchone())
    finally:
        conn.close()


def _provider_owned_by_user(provider_id: int, user_id: int) -> bool:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM ai_providers WHERE id=%s AND created_by=%s LIMIT 1", (provider_id, user_id))
            return bool(cur.fetchone())
    finally:
        conn.close()


def _provider_accessible_by_user(provider_id: int, user_id: int) -> bool:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM ai_providers p
                WHERE p.id=%s
                  AND (
                    p.created_by=%s OR EXISTS(
                      SELECT 1
                      FROM routing_rules r
                      JOIN user_robots ur ON ur.robot_pk=r.robot_pk
                      WHERE r.provider_id=p.id AND ur.user_id=%s
                    )
                  )
                LIMIT 1
                """,
                (provider_id, user_id, user_id),
            )
            return bool(cur.fetchone())
    finally:
        conn.close()


def _resolve_auth_scheme(provider_type: str, auth_scheme: Optional[str]) -> str:
    if auth_scheme:
        return auth_scheme
    return "x-openclaw-token" if provider_type == "openclaw" else "bearer"


def _normalize_extra_json(extra_json: Optional[str]) -> Optional[str]:
    if extra_json is None:
        return None
    s = extra_json.strip()
    if not s:
        return None
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"extra_json 不是有效JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="extra_json 必须是JSON对象")
    return json.dumps(parsed, ensure_ascii=False)


def get_worktool_api_base() -> str:
    if WORKTOOL_API_BASE_FIXED_RAW:
        return normalize_worktool_api_base(WORKTOOL_API_BASE_FIXED_RAW)
    return normalize_worktool_api_base(get_setting("worktool_api_base", WORKTOOL_API_BASE_DEFAULT))


def get_callback_public_base_url() -> str:
    if CALLBACK_PUBLIC_BASE_URL_FIXED_RAW:
        return normalize_public_base_url(CALLBACK_PUBLIC_BASE_URL_FIXED_RAW)
    return normalize_public_base_url(get_setting("callback_public_base_url", ""))


def build_robot_callback_url(robot_id: str) -> str:
    base = get_callback_public_base_url()
    if not base:
        return ""
    return f"{base}/api/v1/callback/qa/{robot_id.strip()}"


async def fetch_worktool_api(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}{path}"
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"worktool request failed: status={resp.status}")
            return data if isinstance(data, dict) else {"data": data}


async def bind_message_callback(robot_id: str, callback_url: str, reply_all: int = 1) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/update"
    timeout = aiohttp.ClientTimeout(total=10)
    payload = {"callBackUrl": callback_url, "replyAll": int(reply_all)}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, params={"robotId": robot_id}, json=payload) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"绑定失败：HTTP {resp.status}")
            if not isinstance(data, dict):
                raise HTTPException(status_code=502, detail="绑定失败：响应格式异常")
            code = str(data.get("code", ""))
            if code not in {"0", "200", ""}:
                msg = data.get("msg") or data.get("message") or "unknown"
                raise HTTPException(status_code=400, detail=f"绑定失败：{msg} (code={code})")
            return data


def _scene_from_room_type(room_type: int) -> str:
    return "group" if int(room_type or 0) in {1, 3} else "private"


def _pick_inbound_text(req: QARequest) -> str:
    return (req.rawSpoken or req.spoken or "").strip()


def _short_text(s: str, n: int = 120) -> str:
    x = (s or "").replace("\n", "\\n").strip()
    return x if len(x) <= n else f"{x[:n]}..."


def _rule_match_target(scene: str, req: QARequest) -> str:
    if scene == "group":
        return ((req.groupName or "").strip() or (req.receivedName or "").strip())
    return (req.receivedName or "").strip()


def _insert_message_log(robot_pk: int, direction: str, scene: str, content: str, status: str) -> None:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO message_logs(robot_pk,direction,scene,normalized_content,status)
                VALUES(%s,%s,%s,%s,%s)
                """,
                (robot_pk, direction, scene, (content or "")[:4000], status),
            )
        conn.commit()
    finally:
        conn.close()


def _load_default_reply(robot_pk: int, scene: str) -> str:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reply_text FROM default_replies WHERE robot_pk=%s AND scene=%s LIMIT 1",
                (robot_pk, scene),
            )
            row = cur.fetchone()
            return ((row or {}).get("reply_text") or "").strip()
    finally:
        conn.close()


def _load_enabled_rules(robot_pk: int, scene: str) -> List[Dict[str, Any]]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id,r.pattern,r.priority,r.provider_id,p.name AS provider_name,p.base_url,p.api_token,p.model,p.provider_type,p.auth_scheme,p.extra_json
                FROM routing_rules r
                JOIN ai_providers p ON p.id=r.provider_id
                WHERE r.robot_pk=%s AND r.scene=%s AND r.enabled=1 AND p.enabled=1
                ORDER BY r.priority ASC, r.id ASC
                """,
                (robot_pk, scene),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


def _extract_provider_text(data: Any) -> str:
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    texts: List[str] = []
                    for x in content:
                        if isinstance(x, dict) and isinstance(x.get("text"), str):
                            texts.append(x["text"])
                    return "".join(texts).strip()
        for key in ("answer", "content", "message", "text"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(data, str):
        return data.strip()
    return ""


async def _call_provider(rule: Dict[str, Any], prompt: str) -> str:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    auth_scheme = str(rule.get("auth_scheme") or "bearer")
    api_token = str(rule.get("api_token") or "")
    if auth_scheme == "bearer" and api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    elif auth_scheme == "x-openclaw-token" and api_token:
        headers["x-openclaw-token"] = api_token

    payload: Dict[str, Any] = {"messages": [{"role": "user", "content": prompt}]}
    model = (rule.get("model") or "").strip() if isinstance(rule.get("model"), str) else ""
    if model:
        payload["model"] = model

    extra_json = rule.get("extra_json")
    if isinstance(extra_json, str) and extra_json.strip():
        try:
            extra_json = json.loads(extra_json)
        except Exception:
            extra_json = None
    if isinstance(extra_json, dict):
        req_headers = extra_json.get("request_headers")
        if isinstance(req_headers, dict):
            for k, v in req_headers.items():
                if isinstance(k, str) and isinstance(v, str):
                    headers[k] = v
        req_body = extra_json.get("request_body")
        if isinstance(req_body, dict):
            payload.update(req_body)

    url = str(rule.get("base_url") or "").strip()
    if not url:
        raise HTTPException(status_code=500, detail="provider base_url empty")

    timeout = aiohttp.ClientTimeout(total=30)
    started = time.perf_counter()
    logger.info(
        "provider_request_start rule_id=%s provider_id=%s provider_name=%s url=%s auth_scheme=%s prompt=%s",
        rule.get("id"),
        rule.get("provider_id"),
        rule.get("provider_name"),
        url,
        auth_scheme,
        _short_text(prompt, 160),
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                logger.warning(
                    "provider_request_http_error rule_id=%s provider_id=%s status=%s cost_ms=%s body=%s",
                    rule.get("id"),
                    rule.get("provider_id"),
                    resp.status,
                    int((time.perf_counter() - started) * 1000),
                    _short_text(raw, 300),
                )
                raise HTTPException(status_code=502, detail=f"provider upstream status={resp.status}")
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = raw
            text = _extract_provider_text(data)
            if not text:
                logger.warning(
                    "provider_request_empty_text rule_id=%s provider_id=%s cost_ms=%s body=%s",
                    rule.get("id"),
                    rule.get("provider_id"),
                    int((time.perf_counter() - started) * 1000),
                    _short_text(raw, 300),
                )
                raise HTTPException(status_code=502, detail="provider response has no text")
            logger.info(
                "provider_request_success rule_id=%s provider_id=%s cost_ms=%s reply=%s",
                rule.get("id"),
                rule.get("provider_id"),
                int((time.perf_counter() - started) * 1000),
                _short_text(text, 160),
            )
            return text


async def _send_worktool_text(robot_id: str, scene: str, req: QARequest, text: str) -> Dict[str, Any]:
    target = _rule_match_target(scene, req)
    if not target:
        raise HTTPException(status_code=400, detail="worktool target empty")
    url = f"{get_worktool_api_base()}/wework/sendRawMessage"
    payload = {
        "socketType": 2,
        "list": [
            {
                "type": 203,
                "titleList": [target],
                "receivedContent": text,
            }
        ],
    }
    started = time.perf_counter()
    logger.info(
        "worktool_send_start robot_id=%s scene=%s target=%s text=%s",
        robot_id,
        scene,
        _short_text(target, 80),
        _short_text(text, 160),
    )
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, params={"robotId": robot_id}, json=payload) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                logger.warning(
                    "worktool_send_http_error robot_id=%s status=%s cost_ms=%s body=%s",
                    robot_id,
                    resp.status,
                    int((time.perf_counter() - started) * 1000),
                    _short_text(raw, 300),
                )
                raise HTTPException(status_code=502, detail=f"worktool sendRawMessage status={resp.status}")
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {"raw": raw}
            code = str((data or {}).get("code", ""))
            if code not in {"0", "200", ""}:
                msg = (data or {}).get("message") or (data or {}).get("msg") or "unknown"
                logger.warning(
                    "worktool_send_business_error robot_id=%s code=%s cost_ms=%s msg=%s body=%s",
                    robot_id,
                    code,
                    int((time.perf_counter() - started) * 1000),
                    _short_text(str(msg), 160),
                    _short_text(raw, 300),
                )
                raise HTTPException(status_code=502, detail=f"worktool sendRawMessage failed: {msg} (code={code})")
            logger.info(
                "worktool_send_success robot_id=%s cost_ms=%s code=%s",
                robot_id,
                int((time.perf_counter() - started) * 1000),
                code or "0",
            )
            return data if isinstance(data, dict) else {"raw": raw}


# ----- lifecycle -----
@app.on_event("startup")
async def startup() -> None:
    init_db()
    logger.info("backend started")


# ----- auth -----
@app.post("/api/v1/auth/sms/send")
async def auth_sms_send(body: SmsSendRequest, request: Request) -> Dict[str, Any]:
    if not AUTH_SMS_ENABLED:
        raise HTTPException(status_code=404, detail="sms auth disabled")
    phone = (body.phone or "").strip()
    if not _is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="手机号格式不合法")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT created_at FROM sms_codes WHERE phone=%s AND scene=%s ORDER BY id DESC LIMIT 1",
                (phone, body.scene),
            )
            latest = cur.fetchone()
            if latest and isinstance(latest.get("created_at"), datetime):
                if (datetime.utcnow() - latest["created_at"]).total_seconds() < 60:
                    raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试")

            cur.execute(
                """
                SELECT COUNT(1) AS c FROM sms_codes
                WHERE phone=%s AND scene=%s AND created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)
                """,
                (phone, body.scene),
            )
            c = int((cur.fetchone() or {}).get("c") or 0)
            if c >= 5:
                raise HTTPException(status_code=429, detail="1小时内发送次数已达上限")

        code = f"{secrets.randbelow(1000000):06d}"
        content = f"{SMS_HUARUI_SIGN}您好，您的验证码是：{code}，该验证码{SMS_CODE_EXPIRE_MINUTES}分钟内有效，请勿泄露。"
        source_ip = (request.client.host if request.client else "") or ""

        upstream_error = ""
        sms_data: Dict[str, Any] = {}
        try:
            sms_data = await _send_sms_via_huarui(phone, content)
        except Exception as e:
            upstream_error = str(e)

        sms_ok = str(sms_data.get("code") or "") == "00000"
        sms_uid = str(sms_data.get("uid") or "-")
        result_json = json.dumps(sms_data if sms_data else {"error": upstream_error}, ensure_ascii=False)[:1024]

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_sms_record(account, source, source_ip, phone, sign, content, send_time, msgid, result)
                VALUES(%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(),%s,%s)
                """,
                (SMS_HUARUI_APPKEY or "-", f"auth:{body.scene}", source_ip, phone, SMS_HUARUI_SIGN, content[:512], sms_uid, result_json),
            )
            if sms_ok:
                cur.execute(
                    """
                    INSERT INTO sms_codes(phone, scene, code_hash, expire_at, request_ip)
                    VALUES(%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s MINUTE),%s)
                    """,
                    (phone, body.scene, _hash_sms_code(code), SMS_CODE_EXPIRE_MINUTES, source_ip),
                )
        conn.commit()
        if not sms_ok:
            raise HTTPException(status_code=502, detail="短信发送失败")
        return {"ok": True, "message": "验证码已发送"}
    finally:
        conn.close()


@app.post("/api/v1/auth/register")
async def auth_register(body: AuthRegisterRequest) -> Dict[str, Any]:
    phone = (body.phone or "").strip()
    code = (body.sms_code or "").strip()
    password = body.password or ""
    company_name = (body.company_name or "").strip() or None

    if not _is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="手机号格式不合法")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少8位")
    if AUTH_SMS_ENABLED and not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="验证码格式不合法")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE phone=%s LIMIT 1", (phone,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="手机号已注册")

            if AUTH_SMS_ENABLED:
                ok = _consume_sms_code(cur, phone, "register", code)
                if not ok:
                    raise HTTPException(status_code=400, detail="验证码错误或已过期")

            cur.execute(
                "INSERT INTO users(phone,password_hash,company_name,token_version,is_active) VALUES(%s,%s,%s,0,1)",
                (phone, _hash_password(password), company_name),
            )
            user_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    token = _create_access_token(user_id, 0)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_days": AUTH_JWT_EXPIRE_DAYS,
        "user": {"id": user_id, "phone": phone, "company_name": company_name},
    }


@app.post("/api/v1/auth/login")
async def auth_login(body: AuthLoginRequest) -> Dict[str, Any]:
    phone = (body.phone or "").strip()
    password = body.password or ""
    if not _is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="手机号格式不合法")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,phone,company_name,password_hash,token_version,is_active FROM users WHERE phone=%s LIMIT 1",
                (phone,),
            )
            user = cur.fetchone()
            if not user or int(user["is_active"]) != 1 or not _verify_password(password, str(user["password_hash"])):
                raise HTTPException(status_code=401, detail="手机号或密码错误")
            cur.execute("UPDATE users SET last_login_at=UTC_TIMESTAMP() WHERE id=%s", (int(user["id"]),))
        conn.commit()
        token = _create_access_token(int(user["id"]), int(user["token_version"]))
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in_days": AUTH_JWT_EXPIRE_DAYS,
            "user": {
                "id": int(user["id"]),
                "phone": user["phone"],
                "company_name": user["company_name"],
                "is_admin": _is_admin_phone(str(user["phone"])),
            },
        }
    finally:
        conn.close()


@app.post("/api/v1/auth/password/reset")
async def auth_reset_password(body: AuthResetPasswordRequest) -> Dict[str, Any]:
    if not AUTH_SMS_ENABLED:
        raise HTTPException(status_code=404, detail="password reset disabled")
    phone = (body.phone or "").strip()
    code = (body.sms_code or "").strip()
    new_password = body.new_password or ""
    if not _is_valid_phone(phone):
        raise HTTPException(status_code=400, detail="手机号格式不合法")
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="验证码格式不合法")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少8位")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE phone=%s LIMIT 1", (phone,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="用户不存在")
            ok = _consume_sms_code(cur, phone, "reset_password", code)
            if not ok:
                raise HTTPException(status_code=400, detail="验证码错误或已过期")

            cur.execute(
                "UPDATE users SET password_hash=%s, token_version=token_version+1 WHERE id=%s",
                (_hash_password(new_password), int(user["id"])),
            )
        conn.commit()
        return {"ok": True, "message": "密码已重置"}
    finally:
        conn.close()


@app.post("/api/v1/auth/logout-all")
async def auth_logout_all(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET token_version=token_version+1 WHERE id=%s", (int(user["id"]),))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "message": "已退出所有设备"}


@app.get("/api/v1/auth/me")
async def auth_me(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    return {
        "id": int(user["id"]),
        "phone": user["phone"],
        "company_name": user["company_name"],
        "is_active": bool(user["is_active"]),
        "is_admin": _is_admin_phone(str(user["phone"])),
        "last_login_at": str(user["last_login_at"]) if user.get("last_login_at") else None,
        "created_at": str(user["created_at"]),
        "updated_at": str(user["updated_at"]),
    }


@app.get("/api/v1/auth/config")
async def auth_config() -> Dict[str, Any]:
    return {
        "sms_auth_enabled": AUTH_SMS_ENABLED,
        "password_reset_enabled": AUTH_SMS_ENABLED,
    }


# ----- basic -----
@app.get("/api/v1/health")
async def health() -> Dict[str, Any]:
    return {"status": "healthy", "version": APP_VERSION, "time": now_iso(), "enable_troubleshoot": ENABLE_TROUBLESHOOT}


@app.get("/api/v1/settings/worktool")
async def get_worktool_settings(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _ = user
    base = get_worktool_api_base()
    callback_public_base_url = get_callback_public_base_url()
    return {
        "worktool_api_base": base,
        "callback_public_base_url": callback_public_base_url,
        "auto_bind_message_callback_on_create": parse_bool(get_setting("auto_bind_message_callback_on_create", "true"), True),
        "runtime_editable": ENABLE_RUNTIME_WORKTOOL_SETTINGS,
        "message_send_api_url": f"{base}/wework/sendRawMessage",
        "callback_example_url": (
            f"{callback_public_base_url}/api/v1/callback/qa/{{robot_id}}" if callback_public_base_url else ""
        ),
    }


@app.put("/api/v1/settings/worktool")
async def update_worktool_settings(body: WorkToolSettingsUpdate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _ = user
    if not ENABLE_RUNTIME_WORKTOOL_SETTINGS:
        raise HTTPException(status_code=403, detail="runtime worktool settings disabled")
    if body.worktool_api_base is not None:
        set_setting("worktool_api_base", normalize_worktool_api_base(body.worktool_api_base))
    if body.callback_public_base_url is not None:
        set_setting("callback_public_base_url", normalize_public_base_url(body.callback_public_base_url))
    if body.auto_bind_message_callback_on_create is not None:
        set_setting("auto_bind_message_callback_on_create", "true" if body.auto_bind_message_callback_on_create else "false")
    return await get_worktool_settings()


@app.get("/api/v1/dashboard/overview")
async def dashboard_overview(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    uid = int(user["id"])
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(1) AS c FROM user_robots WHERE user_id=%s", (uid,))
            robots_total = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute(
                """
                SELECT COUNT(1) AS c
                FROM message_logs ml
                JOIN user_robots ur ON ur.robot_pk=ml.robot_pk
                WHERE ur.user_id=%s AND DATE(ml.created_at)=UTC_DATE() AND ml.direction='inbound'
                """,
                (uid,),
            )
            inbound_today = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute(
                """
                SELECT COUNT(1) AS c
                FROM message_logs ml
                JOIN user_robots ur ON ur.robot_pk=ml.robot_pk
                WHERE ur.user_id=%s AND DATE(ml.created_at)=UTC_DATE() AND ml.direction='outbound' AND ml.status='success'
                """,
                (uid,),
            )
            outbound_success_today = int((cur.fetchone() or {}).get("c") or 0)
            cur.execute(
                """
                SELECT COUNT(1) AS c
                FROM message_logs ml
                JOIN user_robots ur ON ur.robot_pk=ml.robot_pk
                WHERE ur.user_id=%s AND DATE(ml.created_at)=UTC_DATE() AND ml.direction='outbound' AND ml.status='failed'
                """,
                (uid,),
            )
            outbound_fail_today = int((cur.fetchone() or {}).get("c") or 0)
    finally:
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
async def dashboard_trends(days: int = Query(default=7, ge=1, le=90), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    uid = int(user["id"])
    end = datetime.utcnow().date()
    start = end - timedelta(days=days - 1)
    items: Dict[str, Dict[str, Any]] = {}
    for i in range(days):
        d = start + timedelta(days=i)
        k = d.strftime("%Y-%m-%d")
        items[k] = {"date": k, "inbound": 0, "outbound_success": 0}

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DATE(ml.created_at) AS d, ml.direction, ml.status, COUNT(1) AS c
                FROM message_logs ml
                JOIN user_robots ur ON ur.robot_pk=ml.robot_pk
                WHERE ur.user_id=%s AND DATE(ml.created_at) BETWEEN %s AND %s
                GROUP BY DATE(ml.created_at), ml.direction, ml.status
                """,
                (uid, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    for row in rows:
        key = str(row.get("d"))
        if key not in items:
            continue
        c = int(row.get("c") or 0)
        if row.get("direction") == "inbound":
            items[key]["inbound"] += c
        elif row.get("direction") == "outbound" and row.get("status") == "success":
            items[key]["outbound_success"] += c
    return {"days": days, "items": [items[k] for k in sorted(items.keys())]}


# ----- robots/providers/rules -----
@app.get("/api/v1/robots")
async def list_robots(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.* FROM robots r
                JOIN user_robots ur ON ur.robot_pk=r.id
                WHERE ur.user_id=%s
                ORDER BY r.id ASC
                """,
                (int(user["id"]),),
            )
            rows = cur.fetchall() or []
            items = []
            for row in rows:
                row["private_chat_enabled"] = bool(row["private_chat_enabled"])
                row["group_chat_enabled"] = bool(row["group_chat_enabled"])
                row["group_reply_only_when_mentioned"] = bool(row["group_reply_only_when_mentioned"])
                items.append(row)
            return {"items": items}
    finally:
        conn.close()


@app.get("/api/v1/robots/{robot_id}")
async def get_robot(robot_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    row = _require_robot_access(int(user["id"]), robot_id)
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT scene,reply_text FROM default_replies WHERE robot_pk=%s", (int(row["id"]),))
            defaults = cur.fetchall() or []
    finally:
        conn.close()
    row["private_chat_enabled"] = bool(row["private_chat_enabled"])
    row["group_chat_enabled"] = bool(row["group_chat_enabled"])
    row["group_reply_only_when_mentioned"] = bool(row["group_reply_only_when_mentioned"])
    row["defaults"] = {x["scene"]: x["reply_text"] for x in defaults}
    return row


@app.post("/api/v1/robots")
async def create_robot(body: RobotCreate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    rid = (body.robot_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="robot_id required")

    auto_bind = parse_bool(get_setting("auto_bind_message_callback_on_create", "true"), True)
    callback_url = build_robot_callback_url(rid)
    if auto_bind and not callback_url:
        raise HTTPException(status_code=400, detail="create robot failed: 自动绑定消息回调失败：未配置“回调公网基础地址”。")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM robots WHERE robot_id=%s LIMIT 1", (rid,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO user_robots(user_id,robot_pk) VALUES(%s,%s) ON DUPLICATE KEY UPDATE robot_pk=robot_pk",
                    (int(user["id"]), int(row["id"])),
                )
                conn.commit()
                return {"ok": True, "existed": True, "auto_bind_message_callback": False, "callback_url": ""}

            cur.execute(
                """
                INSERT INTO robots(robot_id,name,private_chat_enabled,group_chat_enabled,group_reply_only_when_mentioned,created_by)
                VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (
                    rid,
                    (body.name or "机器人").strip() or "机器人",
                    1 if body.private_chat_enabled else 0,
                    1 if body.group_chat_enabled else 0,
                    1 if body.group_reply_only_when_mentioned else 0,
                    int(user["id"]),
                ),
            )
            robot_pk = int(cur.lastrowid)
            cur.execute(
                "INSERT INTO default_replies(robot_pk,scene,reply_text) VALUES(%s,'group',%s) ON DUPLICATE KEY UPDATE reply_text=VALUES(reply_text)",
                (robot_pk, body.group_default_reply),
            )
            cur.execute(
                "INSERT INTO default_replies(robot_pk,scene,reply_text) VALUES(%s,'private',%s) ON DUPLICATE KEY UPDATE reply_text=VALUES(reply_text)",
                (robot_pk, body.private_default_reply),
            )
            cur.execute("INSERT INTO user_robots(user_id,robot_pk) VALUES(%s,%s)", (int(user["id"]), robot_pk))
        if auto_bind:
            await bind_message_callback(rid, callback_url, 1)
        conn.commit()
        return {"ok": True, "existed": False, "auto_bind_message_callback": auto_bind, "callback_url": callback_url if auto_bind else ""}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


@app.put("/api/v1/robots/{robot_id}")
async def update_robot(robot_id: str, body: RobotUpdate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    robot = _require_robot_access(int(user["id"]), robot_id)
    updates: List[str] = []
    params: List[Any] = []
    if body.name is not None:
        updates.append("name=%s")
        params.append(body.name)
    if body.private_chat_enabled is not None:
        updates.append("private_chat_enabled=%s")
        params.append(1 if body.private_chat_enabled else 0)
    if body.group_chat_enabled is not None:
        updates.append("group_chat_enabled=%s")
        params.append(1 if body.group_chat_enabled else 0)
    if body.group_reply_only_when_mentioned is not None:
        updates.append("group_reply_only_when_mentioned=%s")
        params.append(1 if body.group_reply_only_when_mentioned else 0)

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            if updates:
                params.append(int(robot["id"]))
                cur.execute(f"UPDATE robots SET {', '.join(updates)} WHERE id=%s", tuple(params))
            if body.group_default_reply is not None:
                cur.execute(
                    "INSERT INTO default_replies(robot_pk,scene,reply_text) VALUES(%s,'group',%s) ON DUPLICATE KEY UPDATE reply_text=VALUES(reply_text)",
                    (int(robot["id"]), body.group_default_reply),
                )
            if body.private_default_reply is not None:
                cur.execute(
                    "INSERT INTO default_replies(robot_pk,scene,reply_text) VALUES(%s,'private',%s) ON DUPLICATE KEY UPDATE reply_text=VALUES(reply_text)",
                    (int(robot["id"]), body.private_default_reply),
                )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/v1/robots/{robot_id}")
async def delete_robot(robot_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    robot = _require_robot_access(int(user["id"]), robot_id)
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_robots WHERE user_id=%s AND robot_pk=%s", (int(user["id"]), int(robot["id"])))
            cur.execute("SELECT COUNT(1) AS c FROM user_robots WHERE robot_pk=%s", (int(robot["id"]),))
            remain = int((cur.fetchone() or {}).get("c") or 0)
            if remain == 0:
                cur.execute("DELETE FROM robots WHERE id=%s", (int(robot["id"]),))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/v1/providers")
async def list_providers(robot_id: Optional[str] = None, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _ = robot_id
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT p.*
                FROM ai_providers p
                LEFT JOIN routing_rules r ON r.provider_id=p.id
                LEFT JOIN user_robots ur ON ur.robot_pk=r.robot_pk AND ur.user_id=%s
                WHERE p.created_by=%s OR ur.user_id IS NOT NULL
                ORDER BY p.id ASC
                """,
                (int(user["id"]), int(user["id"])),
            )
            rows = cur.fetchall() or []
            items = []
            for row in rows:
                row["enabled"] = bool(row["enabled"])
                row["api_token_masked"] = mask_token(str(row["api_token"]))
                row.pop("api_token", None)
                items.append(row)
            return {"items": items}
    finally:
        conn.close()


@app.post("/api/v1/providers")
async def create_provider(body: ProviderCreate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    extra = _normalize_extra_json(body.extra_json)

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_providers(created_by,name,base_url,api_token,model,provider_type,auth_scheme,extra_json,enabled)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    int(user["id"]),
                    body.name,
                    body.base_url,
                    body.api_token,
                    body.model,
                    body.provider_type,
                    _resolve_auth_scheme(body.provider_type, body.auth_scheme),
                    extra,
                    1 if body.enabled else 0,
                ),
            )
        conn.commit()
    except pymysql.err.IntegrityError as e:
        raise HTTPException(status_code=400, detail=f"create provider failed: {e}") from e
    finally:
        conn.close()
    return {"ok": True}


@app.put("/api/v1/providers/{provider_id}")
async def update_provider(provider_id: int, body: ProviderUpdate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if not _provider_owned_by_user(provider_id, int(user["id"])):
        raise HTTPException(status_code=403, detail="无权修改该Provider")

    updates: List[str] = []
    params: List[Any] = []
    if body.name is not None:
        updates.append("name=%s")
        params.append(body.name)
    if body.base_url is not None:
        updates.append("base_url=%s")
        params.append(body.base_url)
    if body.api_token is not None:
        updates.append("api_token=%s")
        params.append(body.api_token)
    if body.model is not None:
        updates.append("model=%s")
        params.append(body.model)
    if body.provider_type is not None:
        updates.append("provider_type=%s")
        params.append(body.provider_type)
    if body.auth_scheme is not None:
        updates.append("auth_scheme=%s")
        params.append(body.auth_scheme)
    if body.extra_json is not None:
        updates.append("extra_json=%s")
        params.append(_normalize_extra_json(body.extra_json))
    if body.enabled is not None:
        updates.append("enabled=%s")
        params.append(1 if body.enabled else 0)
    if not updates:
        return {"ok": True}

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            params.append(provider_id)
            params.append(int(user["id"]))
            cur.execute(f"UPDATE ai_providers SET {', '.join(updates)} WHERE id=%s AND created_by=%s", tuple(params))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/v1/providers/{provider_id}")
async def delete_provider(provider_id: int, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if not _provider_owned_by_user(provider_id, int(user["id"])):
        raise HTTPException(status_code=403, detail="无权删除该Provider")
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_providers WHERE id=%s AND created_by=%s", (provider_id, int(user["id"])))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/v1/robots/{robot_id}/rules")
async def list_rules(robot_id: str, scene: Optional[str] = None, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    robot = _require_robot_access(int(user["id"]), robot_id)
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            sql = (
                "SELECT r.id,p.id AS provider_id,p.name AS provider_name,r.scene,r.pattern,r.priority,r.enabled "
                "FROM routing_rules r JOIN ai_providers p ON p.id=r.provider_id WHERE r.robot_pk=%s"
            )
            params: List[Any] = [int(robot["id"])]
            if scene:
                sql += " AND r.scene=%s"
                params.append(scene)
            sql += " ORDER BY r.scene ASC, r.priority ASC, r.id ASC"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []
            items = []
            for row in rows:
                items.append(
                    {
                        "id": int(row["id"]),
                        "robot_id": robot_id,
                        "scene": row["scene"],
                        "pattern": row["pattern"],
                        "provider_id": int(row["provider_id"]),
                        "provider_name": row["provider_name"],
                        "priority": int(row["priority"]),
                        "enabled": bool(row["enabled"]),
                    }
                )
            return {"items": items}
    finally:
        conn.close()


@app.post("/api/v1/rules")
async def create_rule(body: RuleCreate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    robot = _require_robot_access(int(user["id"]), body.robot_id)
    if not _provider_accessible_by_user(body.provider_id, int(user["id"])):
        raise HTTPException(status_code=403, detail="无权使用该Provider")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO routing_rules(robot_pk,scene,pattern,provider_id,priority,enabled)
                VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (int(robot["id"]), body.scene, body.pattern, body.provider_id, body.priority, 1 if body.enabled else 0),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.put("/api/v1/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleUpdate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id,r.robot_pk FROM routing_rules r
                JOIN user_robots ur ON ur.robot_pk=r.robot_pk
                WHERE r.id=%s AND ur.user_id=%s
                LIMIT 1
                """,
                (rule_id, int(user["id"])),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=403, detail="无权修改该规则")

            updates: List[str] = []
            params: List[Any] = []
            if body.pattern is not None:
                updates.append("pattern=%s")
                params.append(body.pattern)
            if body.provider_id is not None:
                if not _provider_accessible_by_user(body.provider_id, int(user["id"])):
                    raise HTTPException(status_code=403, detail="无权使用该Provider")
                updates.append("provider_id=%s")
                params.append(body.provider_id)
            if body.priority is not None:
                updates.append("priority=%s")
                params.append(body.priority)
            if body.enabled is not None:
                updates.append("enabled=%s")
                params.append(1 if body.enabled else 0)
            if updates:
                params.append(rule_id)
                cur.execute(f"UPDATE routing_rules SET {', '.join(updates)} WHERE id=%s", tuple(params))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/v1/rules/{rule_id}")
async def delete_rule(rule_id: int, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE r FROM routing_rules r
                JOIN user_robots ur ON ur.robot_pk=r.robot_pk
                WHERE r.id=%s AND ur.user_id=%s
                """,
                (rule_id, int(user["id"])),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.put("/api/v1/robots/{robot_id}/rules/reorder")
async def reorder_rules(robot_id: str, scene: str, body: ReorderPayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    robot = _require_robot_access(int(user["id"]), robot_id)
    if scene not in {"group", "private"}:
        raise HTTPException(status_code=400, detail="scene must be group/private")
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            for idx, rule_id in enumerate(body.rule_ids, start=1):
                cur.execute(
                    "UPDATE routing_rules SET priority=%s WHERE id=%s AND robot_pk=%s AND scene=%s",
                    (idx, rule_id, int(robot["id"]), scene),
                )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ----- compatible utility endpoints -----
@app.get("/api/v1/logs/messages")
async def list_message_logs(
    robot_id: Optional[str] = None,
    scene: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            where = ["ur.user_id=%s"]
            params: List[Any] = [int(user["id"])]
            if robot_id:
                where.append("r.robot_id=%s")
                params.append(robot_id)
            if scene:
                where.append("ml.scene=%s")
                params.append(scene)
            if status:
                where.append("ml.status=%s")
                params.append(status)
            if direction:
                where.append("ml.direction=%s")
                params.append(direction)

            where_sql = " AND ".join(where)
            cur.execute(
                f"""
                SELECT COUNT(1) AS c
                FROM message_logs ml
                JOIN robots r ON r.id=ml.robot_pk
                JOIN user_robots ur ON ur.robot_pk=r.id
                WHERE {where_sql}
                """,
                tuple(params),
            )
            total = int((cur.fetchone() or {}).get("c") or 0)
            offset = (page - 1) * page_size
            cur.execute(
                f"""
                SELECT ml.id,r.robot_id,ml.direction,ml.scene,ml.normalized_content,ml.status,ml.created_at
                FROM message_logs ml
                JOIN robots r ON r.id=ml.robot_pk
                JOIN user_robots ur ON ur.robot_pk=r.id
                WHERE {where_sql}
                ORDER BY ml.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [page_size, offset]),
            )
            items = cur.fetchall() or []
            return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


@app.get("/api/v1/logs/messages/{log_id}")
async def get_message_log(log_id: int, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ml.id,r.robot_id,ml.direction,ml.scene,ml.normalized_content,ml.status,ml.created_at
                FROM message_logs ml
                JOIN robots r ON r.id=ml.robot_pk
                JOIN user_robots ur ON ur.robot_pk=r.id
                WHERE ml.id=%s AND ur.user_id=%s
                LIMIT 1
                """,
                (log_id, int(user["id"])),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="log not found")
            return row
    finally:
        conn.close()


@app.post("/api/v1/callback/qa/{robot_id}", response_model=QAResponse)
async def qa_callback(robot_id: str, req: QARequest) -> QAResponse:
    robot = _get_robot_by_id_or_404(robot_id)
    robot_pk = int(robot["id"])
    scene = _scene_from_room_type(req.roomType)
    inbound_text = _pick_inbound_text(req)
    match_target = _rule_match_target(scene, req)
    logger.info(
        "qa_callback_received robot_id=%s robot_pk=%s scene=%s room_type=%s at_me=%s match_target=%s text=%s",
        robot_id,
        robot_pk,
        scene,
        req.roomType,
        req.atMe,
        _short_text(match_target, 120),
        _short_text(inbound_text, 200),
    )

    _insert_message_log(robot_pk, "inbound", scene, inbound_text, "received")

    if scene == "private" and not bool(robot.get("private_chat_enabled")):
        logger.info("qa_callback_skipped robot_id=%s reason=private_chat_disabled", robot_id)
        _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
        return QAResponse(code=0, message="参数接收成功")
    if scene == "group":
        if not bool(robot.get("group_chat_enabled")):
            logger.info("qa_callback_skipped robot_id=%s reason=group_chat_disabled", robot_id)
            _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
            return QAResponse(code=0, message="参数接收成功")
        if bool(robot.get("group_reply_only_when_mentioned")) and not bool(req.atMe):
            logger.info("qa_callback_skipped robot_id=%s reason=group_only_when_mentioned", robot_id)
            _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
            return QAResponse(code=0, message="参数接收成功")

    selected_rule: Optional[Dict[str, Any]] = None
    rules = _load_enabled_rules(robot_pk, scene)
    logger.info(
        "qa_callback_rules_loaded robot_id=%s scene=%s rule_count=%s match_target=%s",
        robot_id,
        scene,
        len(rules),
        _short_text(match_target, 120),
    )
    for rule in rules:
        pattern = str(rule.get("pattern") or "")
        if not pattern:
            continue
        try:
            if re.search(pattern, match_target):
                selected_rule = rule
                break
        except re.error:
            logger.warning(
                "qa_callback_rule_invalid_regex robot_id=%s rule_id=%s pattern=%s",
                robot_id,
                rule.get("id"),
                _short_text(pattern, 120),
            )
            continue

    if not selected_rule:
        logger.info("qa_callback_rule_not_matched robot_id=%s scene=%s", robot_id, scene)
        default_reply = _load_default_reply(robot_pk, scene)
        if default_reply:
            logger.info("qa_callback_default_reply robot_id=%s scene=%s reply=%s", robot_id, scene, _short_text(default_reply, 160))
            try:
                await _send_worktool_text(robot_id, scene, req, default_reply)
                _insert_message_log(robot_pk, "outbound", scene, default_reply, "success")
            except Exception as e:
                logger.exception("qa_callback_default_reply_send_failed robot_id=%s scene=%s err=%s", robot_id, scene, str(e))
                _insert_message_log(robot_pk, "outbound", scene, str(e), "failed")
            return QAResponse(code=0, message="参数接收成功")
        _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
        return QAResponse(code=0, message="参数接收成功")

    logger.info(
        "qa_callback_rule_matched robot_id=%s scene=%s rule_id=%s provider_id=%s pattern=%s",
        robot_id,
        scene,
        selected_rule.get("id"),
        selected_rule.get("provider_id"),
        _short_text(str(selected_rule.get("pattern") or ""), 120),
    )
    try:
        reply_text = await _call_provider(selected_rule, inbound_text)
        await _send_worktool_text(robot_id, scene, req, reply_text)
        _insert_message_log(robot_pk, "outbound", scene, reply_text, "success")
        return QAResponse(code=0, message="参数接收成功")
    except Exception as e:
        logger.exception(
            "qa_callback_provider_failed robot_id=%s scene=%s rule_id=%s provider_id=%s err=%s",
            robot_id,
            scene,
            selected_rule.get("id"),
            selected_rule.get("provider_id"),
            str(e),
        )
        _insert_message_log(robot_pk, "outbound", scene, str(e), "failed")
        default_reply = _load_default_reply(robot_pk, scene)
        if default_reply:
            logger.info("qa_callback_fallback_default_reply robot_id=%s scene=%s", robot_id, scene)
            try:
                await _send_worktool_text(robot_id, scene, req, default_reply)
                _insert_message_log(robot_pk, "outbound", scene, default_reply, "success")
            except Exception as e2:
                logger.exception("qa_callback_fallback_send_failed robot_id=%s scene=%s err=%s", robot_id, scene, str(e2))
                _insert_message_log(robot_pk, "outbound", scene, str(e2), "failed")
            return QAResponse(code=0, message="参数接收成功")
        return QAResponse(code=0, message="参数接收成功")


@app.get("/api/v1/worktool/qa-logs")
async def get_worktool_qa_logs(
    robot_id: str,
    page: int = 1,
    size: int = 20,
    sort: str = "start_time,desc",
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), robot_id)
    return await fetch_worktool_api("/robot/qaLog/list", {"robotId": robot_id, "page": page, "size": size, "sort": sort})


@app.get("/api/v1/robot-info/detail")
async def get_robot_info_detail(robot_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), robot_id)
    return await fetch_worktool_api("/robot/robotInfo/get-detail", {"robotId": robot_id})


@app.get("/api/v1/robot-info/callbacks")
async def get_robot_info_callbacks(robot_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), robot_id)
    return await fetch_worktool_api("/robot/robotInfo/callBack/get", {"robotId": robot_id})


@app.get("/api/v1/robot-info/online")
async def get_robot_info_online(robot_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), robot_id)
    return await fetch_worktool_api("/robot/robotInfo/online", {"robotId": robot_id})


@app.get("/api/v1/robot-info/online-infos")
async def get_robot_info_online_infos(robot_id: str, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), robot_id)
    return await fetch_worktool_api("/robot/robotInfo/onlineInfos", {"robotId": robot_id})


@app.post("/api/v1/robot-info/message-callback/test")
async def test_robot_message_callback(body: MessageCallbackPayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), (body.robot_id or "").strip())
    return {"ok": True, "robot_id": body.robot_id, "callback_url": body.callback_url}


@app.post("/api/v1/robot-info/callbacks/test")
async def test_robot_callback(body: CallbackTestPayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _ = user
    return {"ok": True, "callback_url": body.callback_url}


@app.post("/api/v1/robot-info/message-callback/bind")
async def bind_robot_message_callback(body: MessageCallbackPayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    rid = (body.robot_id or "").strip()
    _require_robot_access(int(user["id"]), rid)
    res = await bind_message_callback(rid, (body.callback_url or "").strip(), int(body.reply_all))
    return {"ok": True, "result": res}


@app.post("/api/v1/robot-info/callbacks/bind")
async def bind_robot_callback(body: RobotCallbackBindPayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    rid = (body.robot_id or "").strip()
    _require_robot_access(int(user["id"]), rid)
    # 与 message callback 统一复用
    res = await bind_message_callback(rid, (body.callback_url or "").strip(), 1)
    return {"ok": True, "type": body.type, "result": res}


@app.post("/api/v1/robot-info/callbacks/delete-by-type")
async def delete_robot_callback(body: RobotCallbackDeletePayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_robot_access(int(user["id"]), (body.robot_id or "").strip())
    return {"ok": True, "robot_id": body.robot_id, "type": body.type}


@app.post("/api/v1/troubleshoot/search")
async def troubleshoot_search(body: TroubleshootSearchPayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_admin(user)
    return await run_troubleshoot_search(
        body,
        enable_troubleshoot=ENABLE_TROUBLESHOOT,
        get_worktool_api_base=get_worktool_api_base,
        fetch_worktool_api=fetch_worktool_api,
        db_conn_factory=db_conn,
    )


@app.get("/api/v1/admin/users")
async def admin_list_users(
    phone: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    kw = (phone or "").strip()
    like = f"%{kw}%"
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            if kw:
                cur.execute("SELECT COUNT(1) AS c FROM users WHERE phone LIKE %s", (like,))
            else:
                cur.execute("SELECT COUNT(1) AS c FROM users")
            total = int((cur.fetchone() or {}).get("c") or 0)
            offset = (page - 1) * page_size
            if kw:
                cur.execute(
                    """
                    SELECT
                      u.id,u.phone,u.company_name,u.created_at,u.last_login_at,
                      GROUP_CONCAT(DISTINCT r.robot_id ORDER BY r.robot_id SEPARATOR ',') AS robot_ids
                    FROM users u
                    LEFT JOIN user_robots ur ON ur.user_id=u.id
                    LEFT JOIN robots r ON r.id=ur.robot_pk
                    WHERE u.phone LIKE %s
                    GROUP BY u.id,u.phone,u.company_name,u.created_at,u.last_login_at
                    ORDER BY u.created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (like, page_size, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT
                      u.id,u.phone,u.company_name,u.created_at,u.last_login_at,
                      GROUP_CONCAT(DISTINCT r.robot_id ORDER BY r.robot_id SEPARATOR ',') AS robot_ids
                    FROM users u
                    LEFT JOIN user_robots ur ON ur.user_id=u.id
                    LEFT JOIN robots r ON r.id=ur.robot_pk
                    GROUP BY u.id,u.phone,u.company_name,u.created_at,u.last_login_at
                    ORDER BY u.created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (page_size, offset),
                )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    items = []
    for row in rows:
        ids = (row.get("robot_ids") or "").strip()
        items.append(
            {
                "id": int(row["id"]),
                "phone": row["phone"],
                "company_name": row.get("company_name"),
                "created_at": str(row["created_at"]),
                "last_login_at": str(row["last_login_at"]) if row.get("last_login_at") else None,
                "robot_ids": [x for x in ids.split(",") if x] if ids else [],
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


if __name__ == "__main__":
    import uvicorn

    init_db()
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

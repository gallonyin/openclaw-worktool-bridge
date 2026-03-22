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

DEFAULT_TEST_PROVIDER_ENABLED_RAW = os.getenv("DEFAULT_TEST_PROVIDER_ENABLED", "false").strip().lower()
DEFAULT_TEST_PROVIDER_NAME = os.getenv("DEFAULT_TEST_PROVIDER_NAME", "AI模型(仅测试用)").strip() or "AI模型(仅测试用)"
DEFAULT_TEST_PROVIDER_BASE_URL = os.getenv(
    "DEFAULT_TEST_PROVIDER_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
).strip()
DEFAULT_TEST_PROVIDER_API_KEY = os.getenv("DEFAULT_TEST_PROVIDER_API_KEY", "").strip()
DEFAULT_TEST_PROVIDER_MODEL = os.getenv("DEFAULT_TEST_PROVIDER_MODEL", "doubao-seed-2.0-lite").strip()


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


def _default_test_provider_enabled() -> bool:
    return DEFAULT_TEST_PROVIDER_ENABLED_RAW in {"1", "true", "yes", "on"}


def _db_cfg() -> Dict[str, Any]:
    host = os.getenv("APP_MYSQL_HOST", "").strip()
    port = int(os.getenv("APP_MYSQL_PORT", "3306").strip())
    user = os.getenv("APP_MYSQL_USER", "").strip()
    password = os.getenv("APP_MYSQL_PASSWORD", "")
    database = os.getenv("APP_MYSQL_DATABASE", "").strip()
    app_tz = os.getenv("APP_MYSQL_TIME_ZONE", "+08:00").strip() or "+08:00"
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
        "init_command": f"SET time_zone = '{app_tz}'",
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
                  pattern_match_type ENUM('all','exact','regex') NOT NULL DEFAULT 'regex',
                  pattern VARCHAR(1024) NOT NULL,
                  content_match_type ENUM('all','exact','regex') NOT NULL DEFAULT 'regex',
                  content_pattern VARCHAR(1024) NULL,
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
            cur.execute("SHOW COLUMNS FROM ai_providers LIKE 'is_system'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE ai_providers ADD COLUMN is_system TINYINT(1) NOT NULL DEFAULT 0")
            cur.execute("SHOW COLUMNS FROM routing_rules LIKE 'content_pattern'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE routing_rules ADD COLUMN content_pattern VARCHAR(1024) NULL AFTER pattern")
            cur.execute("SHOW COLUMNS FROM routing_rules LIKE 'pattern_match_type'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE routing_rules ADD COLUMN pattern_match_type ENUM('all','exact','regex') NOT NULL DEFAULT 'regex' AFTER scene"
                )
            cur.execute("SHOW COLUMNS FROM routing_rules LIKE 'content_match_type'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE routing_rules ADD COLUMN content_match_type ENUM('all','exact','regex') NOT NULL DEFAULT 'regex' AFTER pattern"
                )
            try:
                cur.execute("ALTER TABLE ai_providers ADD INDEX idx_provider_is_system (is_system)")
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qa_monitor_logs (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  robot_pk BIGINT NOT NULL,
                  room_type INT NOT NULL DEFAULT 0,
                  text_type INT NOT NULL DEFAULT 1,
                  at_me TINYINT(1) NOT NULL DEFAULT 0,
                  group_name VARCHAR(255) NULL,
                  received_name VARCHAR(255) NULL,
                  question TEXT NULL,
                  answer TEXT NULL,
                  message_id VARCHAR(255) NULL,
                  callback_url VARCHAR(512) NULL,
                  status ENUM('received','success','skipped','failed') NOT NULL DEFAULT 'received',
                  time_cost DECIMAL(10,3) NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX idx_qml_robot_time (robot_pk, created_at),
                  INDEX idx_qml_robot_msg (robot_pk, message_id),
                  CONSTRAINT fk_qml_robot FOREIGN KEY (robot_pk) REFERENCES robots(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS forward_rules (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  created_by BIGINT NOT NULL,
                  source_robot_pk BIGINT NOT NULL,
                  source_scene ENUM('group','private') NOT NULL,
                  source_match_type ENUM('all','exact','regex') NOT NULL DEFAULT 'all',
                  source_pattern VARCHAR(255) NULL,
                  target_name VARCHAR(255) NOT NULL,
                  use_other_robot TINYINT(1) NOT NULL DEFAULT 0,
                  send_robot_pk BIGINT NULL,
                  prefix_enabled TINYINT(1) NOT NULL DEFAULT 1,
                  prefix_template VARCHAR(255) NULL,
                  keyword_match_type ENUM('all','exact','regex') NOT NULL DEFAULT 'all',
                  keyword_pattern VARCHAR(255) NULL,
                  enabled TINYINT(1) NOT NULL DEFAULT 1,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX idx_fr_source (source_robot_pk, source_scene, enabled),
                  INDEX idx_fr_created_by (created_by),
                  CONSTRAINT fk_fr_created_by FOREIGN KEY (created_by) REFERENCES users(id),
                  CONSTRAINT fk_fr_source_robot FOREIGN KEY (source_robot_pk) REFERENCES robots(id) ON DELETE CASCADE,
                  CONSTRAINT fk_fr_send_robot FOREIGN KEY (send_robot_pk) REFERENCES robots(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS forward_logs (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  rule_id BIGINT NOT NULL,
                  source_robot_pk BIGINT NOT NULL,
                  send_robot_pk BIGINT NOT NULL,
                  source_scene ENUM('group','private') NOT NULL,
                  source_name VARCHAR(255) NULL,
                  sender_name VARCHAR(255) NULL,
                  target_name VARCHAR(255) NOT NULL,
                  message_id VARCHAR(255) NULL,
                  question_text TEXT NULL,
                  forwarded_text TEXT NULL,
                  status ENUM('success','failed','skipped') NOT NULL DEFAULT 'success',
                  error_reason VARCHAR(512) NULL,
                  time_cost DECIMAL(10,3) NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX idx_fl_source_robot_time (source_robot_pk, created_at),
                  INDEX idx_fl_rule_time (rule_id, created_at),
                  CONSTRAINT fk_fl_rule FOREIGN KEY (rule_id) REFERENCES forward_rules(id) ON DELETE CASCADE,
                  CONSTRAINT fk_fl_source_robot FOREIGN KEY (source_robot_pk) REFERENCES robots(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SHOW COLUMNS FROM forward_rules LIKE 'target_type'")
            if cur.fetchone():
                cur.execute("ALTER TABLE forward_rules DROP COLUMN target_type")
            cur.execute("SHOW COLUMNS FROM forward_logs LIKE 'target_type'")
            if cur.fetchone():
                cur.execute("ALTER TABLE forward_logs DROP COLUMN target_type")

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


def ensure_default_test_provider(user_id: int) -> None:
    if not _default_test_provider_enabled():
        return
    if not (DEFAULT_TEST_PROVIDER_NAME and DEFAULT_TEST_PROVIDER_BASE_URL and DEFAULT_TEST_PROVIDER_API_KEY and DEFAULT_TEST_PROVIDER_MODEL):
        logger.warning("default test provider enabled but config incomplete, skipped")
        return

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM ai_providers WHERE is_system=1 LIMIT 1")
            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    UPDATE ai_providers
                    SET name=%s,base_url=%s,api_token=%s,model=%s,provider_type='openai',auth_scheme='bearer',enabled=1
                    WHERE id=%s
                    """,
                    (
                        DEFAULT_TEST_PROVIDER_NAME,
                        DEFAULT_TEST_PROVIDER_BASE_URL,
                        DEFAULT_TEST_PROVIDER_API_KEY,
                        DEFAULT_TEST_PROVIDER_MODEL,
                        int(row["id"]),
                    ),
                )
                conn.commit()
                return
            try:
                cur.execute(
                    """
                    INSERT INTO ai_providers(created_by,name,base_url,api_token,model,provider_type,auth_scheme,extra_json,enabled,is_system)
                    VALUES(%s,%s,%s,%s,%s,'openai','bearer',NULL,1,1)
                    """,
                    (
                        int(user_id),
                        DEFAULT_TEST_PROVIDER_NAME,
                        DEFAULT_TEST_PROVIDER_BASE_URL,
                        DEFAULT_TEST_PROVIDER_API_KEY,
                        DEFAULT_TEST_PROVIDER_MODEL,
                    ),
                )
            except pymysql.err.IntegrityError:
                cur.execute("SELECT id FROM ai_providers WHERE name=%s LIMIT 1", (DEFAULT_TEST_PROVIDER_NAME,))
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """
                        UPDATE ai_providers
                        SET is_system=1,base_url=%s,api_token=%s,model=%s,provider_type='openai',auth_scheme='bearer',enabled=1
                        WHERE id=%s
                        """,
                        (
                            DEFAULT_TEST_PROVIDER_BASE_URL,
                            DEFAULT_TEST_PROVIDER_API_KEY,
                            DEFAULT_TEST_PROVIDER_MODEL,
                            int(existing["id"]),
                        ),
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
    # Use Unix epoch milliseconds directly; avoid naive datetime timezone skew.
    ts_ms = int(time.time() * 1000)
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
    messageId: str = ""
    msgId: str = ""


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


class ProviderTestRequest(BaseModel):
    provider_id: Optional[int] = None
    base_url: Optional[str] = None
    api_token: Optional[str] = None
    model: Optional[str] = None
    provider_type: Optional[Literal["openai", "openclaw"]] = None
    auth_scheme: Optional[Literal["bearer", "x-openclaw-token", "none"]] = None
    extra_json: Optional[str] = None


class RuleCreate(BaseModel):
    robot_id: str
    scene: Literal["group", "private"]
    pattern_match_type: Literal["all", "exact", "regex"] = "regex"
    pattern: Optional[str] = None
    content_match_type: Literal["all", "exact", "regex"] = "regex"
    content_pattern: Optional[str] = None
    provider_id: int
    priority: int = 100
    enabled: bool = True


class RuleUpdate(BaseModel):
    pattern_match_type: Optional[Literal["all", "exact", "regex"]] = None
    pattern: Optional[str] = None
    content_match_type: Optional[Literal["all", "exact", "regex"]] = None
    content_pattern: Optional[str] = None
    provider_id: Optional[int] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class ReorderPayload(BaseModel):
    rule_ids: List[int] = Field(default_factory=list)


class ForwardRuleCreate(BaseModel):
    source_robot_id: str
    source_scene: Literal["group", "private"]
    source_match_type: Literal["all", "exact", "regex"] = "all"
    source_pattern: Optional[str] = None
    target_name: str
    use_other_robot: bool = False
    send_robot_id: Optional[str] = None
    prefix_enabled: bool = True
    prefix_template: Optional[str] = None
    keyword_match_type: Literal["all", "exact", "regex"] = "all"
    keyword_pattern: Optional[str] = None
    enabled: bool = True


class ForwardRuleUpdate(BaseModel):
    source_robot_id: Optional[str] = None
    source_scene: Optional[Literal["group", "private"]] = None
    source_match_type: Optional[Literal["all", "exact", "regex"]] = None
    source_pattern: Optional[str] = None
    target_name: Optional[str] = None
    use_other_robot: Optional[bool] = None
    send_robot_id: Optional[str] = None
    prefix_enabled: Optional[bool] = None
    prefix_template: Optional[str] = None
    keyword_match_type: Optional[Literal["all", "exact", "regex"]] = None
    keyword_pattern: Optional[str] = None
    enabled: Optional[bool] = None


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


def _get_robot_by_pk_or_404(robot_pk: int) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM robots WHERE id=%s LIMIT 1", (int(robot_pk),))
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


def _require_robot_access_by_pk(user_id: int, robot_pk: int) -> Dict[str, Any]:
    row = _get_robot_by_pk_or_404(int(robot_pk))
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
            cur.execute("SELECT 1 FROM ai_providers WHERE id=%s AND created_by=%s AND is_system=0 LIMIT 1", (provider_id, user_id))
            return bool(cur.fetchone())
    finally:
        conn.close()


def _provider_accessible_by_user(provider_id: int, user_id: int) -> bool:
    include_system = 1 if _default_test_provider_enabled() else 0
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM ai_providers p
                WHERE p.id=%s
                  AND (
                    (%s=1 AND p.is_system=1) OR
                    p.created_by=%s OR EXISTS(
                      SELECT 1
                      FROM routing_rules r
                      JOIN user_robots ur ON ur.robot_pk=r.robot_pk
                      WHERE r.provider_id=p.id AND ur.user_id=%s
                    )
                  )
                LIMIT 1
                """,
                (provider_id, include_system, user_id, user_id),
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
    # aiohttp query params do not accept None values.
    safe_params = {k: v for k, v in (params or {}).items() if v is not None}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=safe_params) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"worktool request failed: status={resp.status}")
            raw = await resp.text()
            if not raw.strip():
                raise HTTPException(status_code=502, detail="worktool response empty")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                preview = raw.strip().replace("\n", " ")[:200]
                logger.warning(
                    "worktool non-json response path=%s status=%s body_preview=%s",
                    path,
                    resp.status,
                    preview,
                )
                raise HTTPException(status_code=502, detail="worktool response is not valid json")
            return data if isinstance(data, dict) else {"data": data}


async def bind_message_callback(robot_id: str, callback_url: str, reply_all: int = 1) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/update"
    timeout = aiohttp.ClientTimeout(total=10)
    payload = {
        "openCallback": 1,
        "replyAll": int(reply_all),
        "callbackUrl": callback_url,
    }
    logger.info("bind_message_callback request robot_id=%s url=%s payload=%s", robot_id, url, payload)
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


async def bind_callback_by_type(robot_id: str, callback_url: str, callback_type: int) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/callBack/bind"
    timeout = aiohttp.ClientTimeout(total=10)
    payload = {"type": int(callback_type), "callBackUrl": callback_url}
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


async def delete_callback_by_type(robot_id: str, callback_type: int) -> Dict[str, Any]:
    url = f"{get_worktool_api_base()}/robot/robotInfo/callBack/deleteByType"
    timeout = aiohttp.ClientTimeout(total=10)
    payload = {"type": int(callback_type)}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, params={"robotId": robot_id}, json=payload) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"删除回调失败：HTTP {resp.status}")
            if not isinstance(data, dict):
                raise HTTPException(status_code=502, detail="删除回调失败：响应格式异常")
            code = str(data.get("code", ""))
            if code not in {"0", "200", ""}:
                msg = data.get("msg") or data.get("message") or "unknown"
                raise HTTPException(status_code=400, detail=f"删除回调失败：{msg} (code={code})")
            return data


def _extract_callback_url(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("callBackUrl", "callbackUrl", "url"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _flatten_callback_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows: List[Dict[str, Any]] = []
        for k in ("list", "items", "records", "data"):
            v = payload.get(k)
            if isinstance(v, list):
                rows.extend([x for x in v if isinstance(x, dict)])
        if _extract_callback_url(payload):
            rows.append(payload)
        return rows
    return []


async def get_bound_message_callback_url(robot_id: str) -> str:
    res = await fetch_worktool_api("/robot/robotInfo/callBack/get", {"robotId": robot_id})
    rows = _flatten_callback_items(res.get("data"))
    if not rows:
        rows = _flatten_callback_items(res)

    # type=11 代表消息回调，优先读取它。
    for row in rows:
        try:
            callback_type = int(row.get("type"))
        except Exception:
            continue
        if callback_type == 11:
            url = _extract_callback_url(row)
            if url:
                return url
    return ""


async def ensure_default_message_callback(robot_id: str, default_callback_url: str, auto_bind_enabled: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "callback_status": "disabled",
        "auto_bind_message_callback": False,
        "callback_url": "",
        "existing_message_callback_url": "",
    }
    if not auto_bind_enabled:
        result["callback_status"] = "disabled"
        return result
    if not default_callback_url:
        result["callback_status"] = "no_default_url"
        return result

    try:
        existing_url = await get_bound_message_callback_url(robot_id)
    except Exception as e:
        logger.warning("read message callback failed robot_id=%s err=%s", robot_id, e)
        existing_url = ""

    if existing_url:
        result["callback_status"] = "already_bound"
        result["existing_message_callback_url"] = existing_url
        return result

    try:
        await bind_message_callback(robot_id, default_callback_url, 1)
        result["callback_status"] = "bound"
        result["auto_bind_message_callback"] = True
        result["callback_url"] = default_callback_url
        return result
    except Exception as e:
        logger.warning("auto bind message callback failed robot_id=%s err=%s", robot_id, e)
        result["callback_status"] = "bind_failed"
        return result


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


def _pick_message_id(req: QARequest) -> str:
    return (req.messageId or req.msgId or "").strip()


def _normalize_match_pattern(raw_pattern: str) -> str:
    pattern = (raw_pattern or "").strip()
    if not pattern:
        return ""
    # Keep anchored patterns as advanced/exact regex mode.
    if pattern.startswith("^") or pattern.endswith("$"):
        return pattern
    if pattern in {".*", ".*?"}:
        return ".*"
    core = pattern
    changed = True
    while changed and core:
        changed = False
        for prefix in (".*?", ".*"):
            if core.startswith(prefix):
                core = core[len(prefix):]
                changed = True
        for suffix in (".*?", ".*"):
            if core.endswith(suffix):
                core = core[: -len(suffix)]
                changed = True
    if not core:
        return ".*"
    return f".*{re.escape(core)}.*"


def _pattern_matches(raw_pattern: str, text: str) -> bool:
    pattern = _normalize_match_pattern(raw_pattern)
    if not pattern:
        return False
    try:
        return bool(re.search(pattern, text or ""))
    except re.error:
        return False


def _match_with_mode(match_type: str, pattern: str, text: str) -> bool:
    mt = (match_type or "regex").strip().lower()
    if mt == "all":
        return True
    if mt == "exact":
        p = (pattern or "").strip()
        if not p:
            return False
        return (text or "").strip() == p
    return _pattern_matches(pattern, text)


def _mode_rank(match_type: str) -> int:
    mt = (match_type or "regex").strip().lower()
    if mt == "exact":
        return 0
    if mt == "regex":
        return 1
    return 2


def _forward_source_name(scene: str, req: QARequest) -> str:
    if scene == "group":
        return (req.groupName or "").strip()
    return (req.receivedName or "").strip()


def _build_forward_prefix(rule: Dict[str, Any], scene: str, req: QARequest) -> str:
    if not bool(rule.get("prefix_enabled")):
        return ""
    tpl = str(rule.get("prefix_template") or "").strip()
    if not tpl:
        if scene == "group":
            tpl = "[转发自群:{group_name} 提问者:{sender_name}] "
        else:
            tpl = "[转发自:{sender_name}] "
    group_name = (req.groupName or "").strip()
    sender_name = (req.receivedName or "").strip()
    source_name = _forward_source_name(scene, req)
    return (
        tpl.replace("{group_name}", group_name)
        .replace("{sender_name}", sender_name)
        .replace("{source_name}", source_name)
    )


def _insert_forward_log(
    rule_id: int,
    source_robot_pk: int,
    send_robot_pk: int,
    source_scene: str,
    source_name: str,
    sender_name: str,
    target_name: str,
    message_id: str,
    question_text: str,
    forwarded_text: str,
    status: str,
    error_reason: str = "",
    time_cost: Optional[float] = None,
) -> None:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO forward_logs(
                  rule_id,source_robot_pk,send_robot_pk,source_scene,source_name,sender_name,target_name,
                  message_id,question_text,forwarded_text,status,error_reason,time_cost
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    int(rule_id),
                    int(source_robot_pk),
                    int(send_robot_pk),
                    source_scene,
                    source_name[:255],
                    sender_name[:255],
                    target_name[:255],
                    (message_id or "")[:255],
                    (question_text or "")[:4000],
                    (forwarded_text or "")[:4000],
                    status,
                    (error_reason or "")[:512],
                    None if time_cost is None else round(float(time_cost), 3),
                ),
            )
        conn.commit()
    except Exception as e:
        logger.warning("forward_log_insert_failed rule_id=%s err=%s", rule_id, str(e))
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def _load_enabled_forward_rules(source_robot_pk: int, scene: str) -> List[Dict[str, Any]]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fr.*,sr.robot_id AS send_robot_id
                FROM forward_rules fr
                LEFT JOIN robots sr ON sr.id=fr.send_robot_pk
                WHERE fr.source_robot_pk=%s AND fr.source_scene=%s AND fr.enabled=1
                ORDER BY fr.id ASC
                """,
                (int(source_robot_pk), scene),
            )
            return cur.fetchall() or []
    finally:
        conn.close()


async def _run_forwarding_for_callback(robot: Dict[str, Any], scene: str, req: QARequest, inbound_text: str) -> None:
    # v1: only text messages are forwarded.
    if int(req.textType or 0) != 1:
        return
    source_robot_pk = int(robot["id"])
    source_robot_id = str(robot.get("robot_id") or "")
    source_name = _forward_source_name(scene, req)
    sender_name = (req.receivedName or "").strip()
    message_id = _pick_message_id(req)
    rules = _load_enabled_forward_rules(source_robot_pk, scene)
    for rule in rules:
        rule_id = int(rule.get("id") or 0)
        source_match_type = str(rule.get("source_match_type") or "all")
        source_pattern = str(rule.get("source_pattern") or "")
        if not _match_with_mode(source_match_type, source_pattern, source_name):
            continue
        keyword_match_type = str(rule.get("keyword_match_type") or "all")
        keyword_pattern = str(rule.get("keyword_pattern") or "")
        if not _match_with_mode(keyword_match_type, keyword_pattern, inbound_text):
            continue
        send_robot_id = source_robot_id
        send_robot_pk = source_robot_pk
        if bool(rule.get("use_other_robot")) and rule.get("send_robot_id"):
            send_robot_id = str(rule.get("send_robot_id") or "").strip() or source_robot_id
            send_robot_pk = int(rule.get("send_robot_pk") or source_robot_pk)
        target_name = str(rule.get("target_name") or "").strip()
        if not target_name:
            _insert_forward_log(
                rule_id=rule_id,
                source_robot_pk=source_robot_pk,
                send_robot_pk=send_robot_pk,
                source_scene=scene,
                source_name=source_name,
                sender_name=sender_name,
                target_name="",
                message_id=message_id,
                question_text=inbound_text,
                forwarded_text="",
                status="skipped",
                error_reason="target_name empty",
                time_cost=0,
            )
            continue
        prefix = _build_forward_prefix(rule, scene, req)
        forwarded_text = f"{prefix}{inbound_text}"
        started = time.perf_counter()
        try:
            await _send_worktool_text_to_target(send_robot_id, target_name, forwarded_text)
            _insert_forward_log(
                rule_id=rule_id,
                source_robot_pk=source_robot_pk,
                send_robot_pk=send_robot_pk,
                source_scene=scene,
                source_name=source_name,
                sender_name=sender_name,
                target_name=target_name,
                message_id=message_id,
                question_text=inbound_text,
                forwarded_text=forwarded_text,
                status="success",
                time_cost=time.perf_counter() - started,
            )
        except Exception as e:
            logger.warning(
                "forward_send_failed rule_id=%s source_robot=%s send_robot=%s target=%s err=%s",
                rule_id,
                source_robot_id,
                send_robot_id,
                target_name,
                str(e),
            )
            _insert_forward_log(
                rule_id=rule_id,
                source_robot_pk=source_robot_pk,
                send_robot_pk=send_robot_pk,
                source_scene=scene,
                source_name=source_name,
                sender_name=sender_name,
                target_name=target_name,
                message_id=message_id,
                question_text=inbound_text,
                forwarded_text=forwarded_text,
                status="failed",
                error_reason=str(e),
                time_cost=time.perf_counter() - started,
            )


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


def _insert_qa_monitor_log(robot_pk: int, req: QARequest, question: str, callback_url: str) -> int:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO qa_monitor_logs(
                  robot_pk,room_type,text_type,at_me,group_name,received_name,question,answer,message_id,callback_url,status,time_cost
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'received',NULL)
                """,
                (
                    robot_pk,
                    int(req.roomType or 0),
                    int(req.textType or 1),
                    1 if bool(req.atMe) else 0,
                    (req.groupName or "").strip() or None,
                    (req.receivedName or "").strip() or None,
                    (question or "")[:4000],
                    "",
                    _pick_message_id(req) or None,
                    callback_url or None,
                ),
            )
            row_id = int(cur.lastrowid)
        conn.commit()
        return row_id
    except Exception as e:
        logger.warning("qa_monitor_log_insert_failed robot_pk=%s err=%s", robot_pk, str(e))
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        conn.close()


def _is_duplicate_qa_callback(robot_pk: int, req: QARequest, question: str, window_seconds: int = 8) -> bool:
    room_type = int(req.roomType or 0)
    text_type = int(req.textType or 1)
    received_name = (req.receivedName or "").strip()
    group_name = (req.groupName or "").strip()
    message_id = _pick_message_id(req)
    question = (question or "").strip()
    if not question:
        return False
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            # Priority 1: strong de-dup by message_id when available.
            if message_id:
                cur.execute(
                    """
                    SELECT id
                    FROM qa_monitor_logs
                    WHERE robot_pk=%s
                      AND COALESCE(message_id,'')=%s
                      AND created_at >= DATE_SUB(NOW(), INTERVAL 120 SECOND)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(robot_pk), message_id),
                )
                if cur.fetchone():
                    return True

                # Some callbacks arrive first without message_id then with message_id seconds later.
                cur.execute(
                    """
                    SELECT id
                    FROM qa_monitor_logs
                    WHERE robot_pk=%s
                      AND room_type=%s
                      AND COALESCE(received_name,'')=%s
                      AND COALESCE(group_name,'')=%s
                      AND question=%s
                      AND COALESCE(message_id,'')=''
                      AND created_at >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        int(robot_pk),
                        room_type,
                        received_name,
                        group_name,
                        question,
                        max(int(window_seconds), 1),
                    ),
                )
                if cur.fetchone():
                    return True

            params: List[Any] = [
                int(robot_pk),
                room_type,
                text_type,
                received_name,
                group_name,
                question,
                max(int(window_seconds), 1),
            ]
            sql = (
                """
                SELECT id
                FROM qa_monitor_logs
                WHERE robot_pk=%s
                  AND room_type=%s
                  AND text_type=%s
                  AND COALESCE(received_name,'')=%s
                  AND COALESCE(group_name,'')=%s
                  AND question=%s
                  AND created_at >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                """
            )
            sql += " ORDER BY id DESC LIMIT 1"
            cur.execute(sql, tuple(params))
            return cur.fetchone() is not None
    except Exception as e:
        logger.warning("qa_callback_duplicate_check_failed robot_pk=%s err=%s", robot_pk, str(e))
        return False
    finally:
        conn.close()


def _update_qa_monitor_log(row_id: Optional[int], answer: str, status: str, time_cost: Optional[float] = None) -> None:
    if not row_id:
        return
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE qa_monitor_logs SET answer=%s,status=%s,time_cost=%s WHERE id=%s",
                ((answer or "")[:4000], status, None if time_cost is None else round(float(time_cost), 3), int(row_id)),
            )
        conn.commit()
    except Exception as e:
        logger.warning("qa_monitor_log_update_failed row_id=%s err=%s", row_id, str(e))
        try:
            conn.rollback()
        except Exception:
            pass
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
                SELECT r.id,r.pattern_match_type,r.pattern,r.content_match_type,r.content_pattern,r.priority,r.provider_id,p.name AS provider_name,p.base_url,p.api_token,p.model,p.provider_type,p.auth_scheme,p.extra_json
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


async def _send_worktool_text_to_target(robot_id: str, target: str, text: str) -> Dict[str, Any]:
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
        "worktool_send_start robot_id=%s target=%s text=%s",
        robot_id,
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


async def _send_worktool_text(robot_id: str, scene: str, req: QARequest, text: str) -> Dict[str, Any]:
    target = _rule_match_target(scene, req)
    return await _send_worktool_text_to_target(robot_id, target, text)


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
                if (datetime.now() - latest["created_at"]).total_seconds() < 60:
                    raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试")

            cur.execute(
                """
                SELECT COUNT(1) AS c FROM sms_codes
                WHERE phone=%s AND scene=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
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
                VALUES(%s,%s,%s,%s,%s,%s,NOW(),%s,%s)
                """,
                (SMS_HUARUI_APPKEY or "-", f"auth:{body.scene}", source_ip, phone, SMS_HUARUI_SIGN, content[:512], sms_uid, result_json),
            )
            if sms_ok:
                cur.execute(
                    """
                    INSERT INTO sms_codes(phone, scene, code_hash, expire_at, request_ip)
                    VALUES(%s,%s,%s,DATE_ADD(NOW(), INTERVAL %s MINUTE),%s)
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
    existed = False

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM robots WHERE robot_id=%s LIMIT 1", (rid,))
            row = cur.fetchone()
            if row:
                existed = True
                cur.execute(
                    "INSERT INTO user_robots(user_id,robot_pk) VALUES(%s,%s) ON DUPLICATE KEY UPDATE robot_pk=robot_pk",
                    (int(user["id"]), int(row["id"])),
                )
            else:
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
        conn.commit()
        callback_result = await ensure_default_message_callback(
            robot_id=rid,
            default_callback_url=callback_url,
            auto_bind_enabled=auto_bind,
        )
        return {"ok": True, "existed": existed, **callback_result}
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
    ensure_default_test_provider(int(user["id"]))
    include_system = 1 if _default_test_provider_enabled() else 0
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT p.*
                FROM ai_providers p
                LEFT JOIN routing_rules r ON r.provider_id=p.id
                LEFT JOIN user_robots ur ON ur.robot_pk=r.robot_pk AND ur.user_id=%s
                WHERE (%s=1 AND p.is_system=1) OR p.created_by=%s OR ur.user_id IS NOT NULL
                ORDER BY p.id ASC
                """,
                (int(user["id"]), include_system, int(user["id"])),
            )
            rows = cur.fetchall() or []
            items = []
            for row in rows:
                is_system = bool(row.get("is_system"))
                can_manage = (not is_system) and int(row.get("created_by") or 0) == int(user["id"])
                row["enabled"] = bool(row["enabled"])
                row["is_system"] = is_system
                row["can_manage"] = can_manage
                row["api_token_masked"] = mask_token(str(row["api_token"]))
                row.pop("api_token", None)
                row.pop("created_by", None)
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


@app.post("/api/v1/providers/test")
async def test_provider(body: ProviderTestRequest, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    uid = int(user["id"])

    if body.provider_id is not None:
        provider_id = int(body.provider_id)
        if not _provider_owned_by_user(provider_id, uid):
            raise HTTPException(status_code=403, detail="无权测试该Provider")
        conn = db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM ai_providers WHERE id=%s AND created_by=%s LIMIT 1", (provider_id, uid))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Provider 不存在")
                cfg = dict(row)
        finally:
            conn.close()

    if body.base_url is not None:
        cfg["base_url"] = (body.base_url or "").strip()
    if body.model is not None:
        cfg["model"] = body.model
    if body.provider_type is not None:
        cfg["provider_type"] = body.provider_type
    if body.auth_scheme is not None:
        cfg["auth_scheme"] = body.auth_scheme
    if body.extra_json is not None:
        cfg["extra_json"] = _normalize_extra_json(body.extra_json)
    if body.api_token is not None:
        token = (body.api_token or "").strip()
        if token:
            cfg["api_token"] = token
        elif not cfg.get("api_token"):
            raise HTTPException(status_code=400, detail="API Token 不能为空")

    base_url = str(cfg.get("base_url") or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="Base URL 不能为空")

    api_token = str(cfg.get("api_token") or "").strip()
    if not api_token:
        raise HTTPException(status_code=400, detail="API Token 不能为空")

    provider_type = str(cfg.get("provider_type") or "openai")
    auth_scheme = _resolve_auth_scheme(provider_type, cfg.get("auth_scheme"))
    extra_json = cfg.get("extra_json")
    if isinstance(extra_json, dict):
        extra_json = json.dumps(extra_json, ensure_ascii=False)

    test_rule = {
        "id": cfg.get("id") or 0,
        "provider_id": cfg.get("id") or 0,
        "provider_name": cfg.get("name") or "provider_test",
        "base_url": base_url,
        "api_token": api_token,
        "model": cfg.get("model") or "",
        "provider_type": provider_type,
        "auth_scheme": auth_scheme,
        "extra_json": extra_json,
    }
    started = time.perf_counter()
    reply = await _call_provider(test_rule, "hi")
    elapsed = round(time.perf_counter() - started, 3)
    return {"ok": True, "elapsed_seconds": elapsed, "reply_preview": _short_text(reply, 200)}


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
                "SELECT r.id,p.id AS provider_id,p.name AS provider_name,r.scene,"
                "r.pattern_match_type,r.pattern,r.content_match_type,r.content_pattern,r.priority,r.enabled "
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
                        "pattern_match_type": row.get("pattern_match_type") or "regex",
                        "pattern": row["pattern"],
                        "content_match_type": row.get("content_match_type") or "regex",
                        "content_pattern": row.get("content_pattern"),
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
    pattern_match_type = body.pattern_match_type
    content_match_type = body.content_match_type
    title_pattern = (body.pattern or "").strip()
    content_pattern = (body.content_pattern or "").strip()
    if pattern_match_type != "all" and not title_pattern:
        raise HTTPException(status_code=400, detail="群名/昵称匹配方式为精准/模糊时，请填写匹配内容")
    if content_match_type != "all" and not content_pattern:
        raise HTTPException(status_code=400, detail="聊天内容匹配方式为精准/模糊时，请填写匹配内容")
    if pattern_match_type != "all" and content_match_type != "all" and not title_pattern and not content_pattern:
        raise HTTPException(status_code=400, detail="请至少填写一个匹配规则（群名/昵称 或 聊天内容）")

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO routing_rules(robot_pk,scene,pattern_match_type,pattern,content_match_type,content_pattern,provider_id,priority,enabled)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    int(robot["id"]),
                    body.scene,
                    pattern_match_type,
                    title_pattern,
                    content_match_type,
                    content_pattern or None,
                    body.provider_id,
                    body.priority,
                    1 if body.enabled else 0,
                ),
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
                SELECT r.id,r.robot_pk,r.pattern_match_type,r.pattern,r.content_match_type,r.content_pattern FROM routing_rules r
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
            if body.pattern_match_type is not None:
                updates.append("pattern_match_type=%s")
                params.append(body.pattern_match_type)
            if body.pattern is not None:
                updates.append("pattern=%s")
                params.append(body.pattern.strip())
            if body.content_match_type is not None:
                updates.append("content_match_type=%s")
                params.append(body.content_match_type)
            if body.content_pattern is not None:
                updates.append("content_pattern=%s")
                params.append(body.content_pattern.strip() or None)
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
            if body.pattern is not None or body.content_pattern is not None or body.pattern_match_type is not None or body.content_match_type is not None:
                next_pattern_match_type = (
                    body.pattern_match_type
                    if body.pattern_match_type is not None
                    else str(row.get("pattern_match_type") or "regex")
                )
                next_content_match_type = (
                    body.content_match_type
                    if body.content_match_type is not None
                    else str(row.get("content_match_type") or "regex")
                )
                next_title_pattern = body.pattern.strip() if body.pattern is not None else str(row.get("pattern") or "").strip()
                next_content_pattern = (
                    body.content_pattern.strip() if body.content_pattern is not None else str(row.get("content_pattern") or "").strip()
                )
                if next_pattern_match_type != "all" and not next_title_pattern:
                    raise HTTPException(status_code=400, detail="群名/昵称匹配方式为精准/模糊时，请填写匹配内容")
                if next_content_match_type != "all" and not next_content_pattern:
                    raise HTTPException(status_code=400, detail="聊天内容匹配方式为精准/模糊时，请填写匹配内容")
                if (
                    next_pattern_match_type != "all"
                    and next_content_match_type != "all"
                    and not next_title_pattern
                    and not next_content_pattern
                ):
                    raise HTTPException(status_code=400, detail="请至少填写一个匹配规则（群名/昵称 或 聊天内容）")
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


@app.get("/api/v1/forwards")
async def list_forward_rules(
    source_robot_id: Optional[str] = None,
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    uid = int(user["id"])
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            sql = (
                """
                SELECT fr.*,
                       sr.robot_id AS source_robot_id,sr.name AS source_robot_name,
                       rr.robot_id AS send_robot_id,rr.name AS send_robot_name
                FROM forward_rules fr
                JOIN robots sr ON sr.id=fr.source_robot_pk
                LEFT JOIN robots rr ON rr.id=fr.send_robot_pk
                JOIN user_robots ur ON ur.robot_pk=fr.source_robot_pk AND ur.user_id=%s
                WHERE fr.created_by=%s
                """
            )
            params: List[Any] = [uid, uid]
            if source_robot_id:
                sql += " AND sr.robot_id=%s"
                params.append(source_robot_id)
            sql += " ORDER BY fr.id DESC"
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []
            items: List[Dict[str, Any]] = []
            for row in rows:
                items.append(
                    {
                        "id": int(row["id"]),
                        "source_robot_id": row.get("source_robot_id"),
                        "source_robot_name": row.get("source_robot_name") or "",
                        "source_scene": row.get("source_scene"),
                        "source_match_type": row.get("source_match_type"),
                        "source_pattern": row.get("source_pattern") or "",
                        "target_name": row.get("target_name") or "",
                        "use_other_robot": bool(row.get("use_other_robot")),
                        "send_robot_id": row.get("send_robot_id"),
                        "send_robot_name": row.get("send_robot_name") or "",
                        "prefix_enabled": bool(row.get("prefix_enabled")),
                        "prefix_template": row.get("prefix_template") or "",
                        "keyword_match_type": row.get("keyword_match_type"),
                        "keyword_pattern": row.get("keyword_pattern") or "",
                        "enabled": bool(row.get("enabled")),
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                    }
                )
            return {"items": items}
    finally:
        conn.close()


@app.post("/api/v1/forwards")
async def create_forward_rule(body: ForwardRuleCreate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    uid = int(user["id"])
    source_robot = _require_robot_access(uid, body.source_robot_id)
    source_match_type = body.source_match_type
    source_pattern = (body.source_pattern or "").strip()
    if source_match_type != "all" and not source_pattern:
        raise HTTPException(status_code=400, detail="来源对象匹配为精准/模糊时，请填写来源对象")
    target_name = (body.target_name or "").strip()
    if not target_name:
        raise HTTPException(status_code=400, detail="目标名称不能为空")
    keyword_match_type = body.keyword_match_type
    keyword_pattern = (body.keyword_pattern or "").strip()
    if keyword_match_type != "all" and not keyword_pattern:
        raise HTTPException(status_code=400, detail="关键词匹配为精准/模糊时，请填写关键词")
    send_robot_pk: Optional[int] = None
    if body.use_other_robot:
        if not (body.send_robot_id or "").strip():
            raise HTTPException(status_code=400, detail="已开启“使用其他机器人发送”，请先选择发送机器人")
        send_robot = _require_robot_access(uid, body.send_robot_id or "")
        send_robot_pk = int(send_robot["id"])
    prefix_template = (body.prefix_template or "").strip() or None

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO forward_rules(
                  created_by,source_robot_pk,source_scene,source_match_type,source_pattern,target_name,
                  use_other_robot,send_robot_pk,prefix_enabled,prefix_template,keyword_match_type,keyword_pattern,enabled
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    uid,
                    int(source_robot["id"]),
                    body.source_scene,
                    source_match_type,
                    source_pattern or None,
                    target_name,
                    1 if body.use_other_robot else 0,
                    send_robot_pk,
                    1 if body.prefix_enabled else 0,
                    prefix_template,
                    keyword_match_type,
                    keyword_pattern or None,
                    1 if body.enabled else 0,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.put("/api/v1/forwards/{rule_id}")
async def update_forward_rule(rule_id: int, body: ForwardRuleUpdate, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    uid = int(user["id"])
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM forward_rules WHERE id=%s AND created_by=%s LIMIT 1", (rule_id, uid))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="转发规则不存在")

            source_robot_pk = int(row["source_robot_pk"])
            if body.source_robot_id is not None:
                source_robot = _require_robot_access(uid, body.source_robot_id)
                source_robot_pk = int(source_robot["id"])

            source_scene = body.source_scene if body.source_scene is not None else row.get("source_scene")
            source_match_type = body.source_match_type if body.source_match_type is not None else row.get("source_match_type")
            source_pattern = (
                body.source_pattern.strip()
                if body.source_pattern is not None
                else str(row.get("source_pattern") or "").strip()
            )
            if source_match_type != "all" and not source_pattern:
                raise HTTPException(status_code=400, detail="来源对象匹配为精准/模糊时，请填写来源对象")

            target_name = (
                body.target_name.strip()
                if body.target_name is not None
                else str(row.get("target_name") or "").strip()
            )
            if not target_name:
                raise HTTPException(status_code=400, detail="目标名称不能为空")

            use_other_robot = bool(body.use_other_robot) if body.use_other_robot is not None else bool(row.get("use_other_robot"))
            send_robot_pk: Optional[int]
            if use_other_robot:
                target_send_robot_id = (
                    (body.send_robot_id or "").strip()
                    if body.send_robot_id is not None
                    else (
                        _get_robot_by_pk_or_404(int(row["send_robot_pk"])).get("robot_id")
                        if row.get("send_robot_pk")
                        else ""
                    )
                )
                if not target_send_robot_id:
                    raise HTTPException(status_code=400, detail="已开启“使用其他机器人发送”，请先选择发送机器人")
                send_robot = _require_robot_access(uid, target_send_robot_id)
                send_robot_pk = int(send_robot["id"])
            else:
                send_robot_pk = None

            prefix_enabled = bool(body.prefix_enabled) if body.prefix_enabled is not None else bool(row.get("prefix_enabled"))
            prefix_template = (
                body.prefix_template.strip()
                if body.prefix_template is not None
                else str(row.get("prefix_template") or "").strip()
            ) or None
            keyword_match_type = (
                body.keyword_match_type if body.keyword_match_type is not None else row.get("keyword_match_type")
            )
            keyword_pattern = (
                body.keyword_pattern.strip()
                if body.keyword_pattern is not None
                else str(row.get("keyword_pattern") or "").strip()
            )
            if keyword_match_type != "all" and not keyword_pattern:
                raise HTTPException(status_code=400, detail="关键词匹配为精准/模糊时，请填写关键词")
            enabled = bool(body.enabled) if body.enabled is not None else bool(row.get("enabled"))

            cur.execute(
                """
                UPDATE forward_rules
                SET source_robot_pk=%s,source_scene=%s,source_match_type=%s,source_pattern=%s,
                    target_name=%s,use_other_robot=%s,send_robot_pk=%s,
                    prefix_enabled=%s,prefix_template=%s,keyword_match_type=%s,keyword_pattern=%s,enabled=%s
                WHERE id=%s AND created_by=%s
                """,
                (
                    source_robot_pk,
                    source_scene,
                    source_match_type,
                    source_pattern or None,
                    target_name,
                    1 if use_other_robot else 0,
                    send_robot_pk,
                    1 if prefix_enabled else 0,
                    prefix_template,
                    keyword_match_type,
                    keyword_pattern or None,
                    1 if enabled else 0,
                    rule_id,
                    uid,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/v1/forwards/{rule_id}")
async def delete_forward_rule(rule_id: int, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM forward_rules WHERE id=%s AND created_by=%s", (rule_id, int(user["id"])))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/v1/forwards/logs")
async def list_forward_logs(
    robot_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    uid = int(user["id"])
    conn = db_conn()
    try:
        with conn.cursor() as cur:
            where_parts = ["fr.created_by=%s"]
            params: List[Any] = [uid]
            if robot_id:
                source_robot = _require_robot_access(uid, robot_id)
                where_parts.append("fl.source_robot_pk=%s")
                params.append(int(source_robot["id"]))
            where_sql = " AND ".join(where_parts)
            cur.execute(
                f"""
                SELECT COUNT(1) AS c
                FROM forward_logs fl
                JOIN forward_rules fr ON fr.id=fl.rule_id
                WHERE {where_sql}
                """,
                tuple(params),
            )
            total = int((cur.fetchone() or {}).get("c") or 0)
            offset = (page - 1) * page_size
            cur.execute(
                f"""
                SELECT fl.*,sr.robot_id AS source_robot_id,rr.robot_id AS send_robot_id
                FROM forward_logs fl
                JOIN forward_rules fr ON fr.id=fl.rule_id
                JOIN robots sr ON sr.id=fl.source_robot_pk
                JOIN robots rr ON rr.id=fl.send_robot_pk
                WHERE {where_sql}
                ORDER BY fl.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [page_size, offset]),
            )
            rows = cur.fetchall() or []
            items: List[Dict[str, Any]] = []
            for row in rows:
                items.append(
                    {
                        "id": int(row["id"]),
                        "rule_id": int(row["rule_id"]),
                        "source_robot_id": row.get("source_robot_id"),
                        "send_robot_id": row.get("send_robot_id"),
                        "source_scene": row.get("source_scene"),
                        "source_name": row.get("source_name") or "",
                        "sender_name": row.get("sender_name") or "",
                        "target_name": row.get("target_name") or "",
                        "message_id": row.get("message_id") or "",
                        "question_text": row.get("question_text") or "",
                        "forwarded_text": row.get("forwarded_text") or "",
                        "status": row.get("status"),
                        "error_reason": row.get("error_reason") or "",
                        "time_cost": float(row.get("time_cost") or 0),
                        "created_at": row.get("created_at"),
                    }
                )
            return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


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


async def _is_platform_message_callback(robot_id: str) -> bool:
    expected = build_robot_callback_url(robot_id).strip().rstrip("/")
    if not expected:
        return False
    try:
        res = await fetch_worktool_api("/robot/robotInfo/callBack/get", {"robotId": robot_id})
    except Exception as e:
        logger.warning("detect_message_callback_source_failed robot_id=%s err=%s", robot_id, str(e))
        return False
    rows = res.get("data")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            cb_type = int(row.get("type"))
        except Exception:
            continue
        if cb_type != 11:
            continue
        current = str(row.get("callBackUrl") or row.get("callbackUrl") or "").strip().rstrip("/")
        return bool(current) and current == expected
    return False


@app.post("/api/v1/callback/qa/{robot_id}", response_model=QAResponse)
async def qa_callback(robot_id: str, req: QARequest, request: Request) -> QAResponse:
    started_at = time.perf_counter()
    robot = _get_robot_by_id_or_404(robot_id)
    robot_pk = int(robot["id"])
    scene = _scene_from_room_type(req.roomType)
    inbound_text = _pick_inbound_text(req)
    match_target = _rule_match_target(scene, req)
    callback_message_id = _pick_message_id(req)

    # Only text callbacks should trigger QA reply pipeline.
    if int(req.textType or 0) != 1:
        logger.info(
            "qa_callback_non_text_ignored robot_id=%s robot_pk=%s scene=%s room_type=%s text_type=%s message_id=%s",
            robot_id,
            robot_pk,
            scene,
            req.roomType,
            req.textType,
            callback_message_id or "-",
        )
        return QAResponse(code=0, message="参数接收成功")

    if _is_duplicate_qa_callback(robot_pk, req, inbound_text):
        logger.info(
            "qa_callback_duplicate_ignored robot_id=%s robot_pk=%s scene=%s room_type=%s message_id=%s match_target=%s text=%s",
            robot_id,
            robot_pk,
            scene,
            req.roomType,
            callback_message_id or "-",
            _short_text(match_target, 120),
            _short_text(inbound_text, 200),
        )
        return QAResponse(code=0, message="参数接收成功")

    local_log_id = _insert_qa_monitor_log(robot_pk, req, inbound_text, str(request.url))
    logger.info(
        "qa_callback_received robot_id=%s robot_pk=%s scene=%s room_type=%s at_me=%s message_id=%s match_target=%s text=%s",
        robot_id,
        robot_pk,
        scene,
        req.roomType,
        req.atMe,
        callback_message_id or "-",
        _short_text(match_target, 120),
        _short_text(inbound_text, 200),
    )

    _insert_message_log(robot_pk, "inbound", scene, inbound_text, "received")
    try:
        await _run_forwarding_for_callback(robot, scene, req, inbound_text)
    except Exception as e:
        logger.warning("forwarding_pipeline_failed robot_id=%s err=%s", robot_id, str(e))

    if scene == "private" and not bool(robot.get("private_chat_enabled")):
        logger.info("qa_callback_skipped robot_id=%s reason=private_chat_disabled", robot_id)
        _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
        _update_qa_monitor_log(local_log_id, "", "skipped", time.perf_counter() - started_at)
        return QAResponse(code=0, message="参数接收成功")
    if scene == "group":
        if not bool(robot.get("group_chat_enabled")):
            logger.info("qa_callback_skipped robot_id=%s reason=group_chat_disabled", robot_id)
            _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
            _update_qa_monitor_log(local_log_id, "", "skipped", time.perf_counter() - started_at)
            return QAResponse(code=0, message="参数接收成功")
        if bool(robot.get("group_reply_only_when_mentioned")) and not bool(req.atMe):
            logger.info("qa_callback_skipped robot_id=%s reason=group_only_when_mentioned", robot_id)
            _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
            _update_qa_monitor_log(local_log_id, "", "skipped", time.perf_counter() - started_at)
            return QAResponse(code=0, message="参数接收成功")

    selected_rule: Optional[Dict[str, Any]] = None
    selected_rank: Optional[int] = None
    rules = _load_enabled_rules(robot_pk, scene)
    logger.info(
        "qa_callback_rules_loaded robot_id=%s scene=%s rule_count=%s match_target=%s",
        robot_id,
        scene,
        len(rules),
        _short_text(match_target, 120),
    )
    for rule in rules:
        title_match_type = str(rule.get("pattern_match_type") or "regex")
        content_match_type = str(rule.get("content_match_type") or "regex")
        title_pattern = str(rule.get("pattern") or "")
        content_pattern = str(rule.get("content_pattern") or "")
        matched_title = _match_with_mode(title_match_type, title_pattern, match_target)
        matched_content = _match_with_mode(content_match_type, content_pattern, inbound_text)
        if matched_title or matched_content:
            candidate_rank = 99
            if matched_title:
                candidate_rank = min(candidate_rank, _mode_rank(title_match_type))
            if matched_content:
                candidate_rank = min(candidate_rank, _mode_rank(content_match_type))
            if selected_rule is None:
                selected_rule = rule
                selected_rank = candidate_rank
                continue
            current_priority = int(selected_rule.get("priority") or 999999)
            candidate_priority = int(rule.get("priority") or 999999)
            current_id = int(selected_rule.get("id") or 0)
            candidate_id = int(rule.get("id") or 0)
            if (
                selected_rank is None
                or candidate_rank < selected_rank
                or (
                    candidate_rank == selected_rank
                    and (
                        candidate_priority < current_priority
                        or (candidate_priority == current_priority and candidate_id < current_id)
                    )
                )
            ):
                selected_rule = rule
                selected_rank = candidate_rank

    if not selected_rule:
        logger.info("qa_callback_rule_not_matched robot_id=%s scene=%s", robot_id, scene)
        default_reply = _load_default_reply(robot_pk, scene)
        if default_reply:
            logger.info("qa_callback_default_reply robot_id=%s scene=%s reply=%s", robot_id, scene, _short_text(default_reply, 160))
            try:
                await _send_worktool_text(robot_id, scene, req, default_reply)
                _insert_message_log(robot_pk, "outbound", scene, default_reply, "success")
                _update_qa_monitor_log(local_log_id, default_reply, "success", time.perf_counter() - started_at)
            except Exception as e:
                logger.exception("qa_callback_default_reply_send_failed robot_id=%s scene=%s err=%s", robot_id, scene, str(e))
                _insert_message_log(robot_pk, "outbound", scene, str(e), "failed")
                _update_qa_monitor_log(local_log_id, str(e), "failed", time.perf_counter() - started_at)
            return QAResponse(code=0, message="参数接收成功")
        _insert_message_log(robot_pk, "outbound", scene, "", "skipped")
        _update_qa_monitor_log(local_log_id, "", "skipped", time.perf_counter() - started_at)
        return QAResponse(code=0, message="参数接收成功")

    logger.info(
        "qa_callback_rule_matched robot_id=%s scene=%s rule_id=%s provider_id=%s title_match_type=%s content_match_type=%s title_pattern=%s content_pattern=%s selected_rank=%s",
        robot_id,
        scene,
        selected_rule.get("id"),
        selected_rule.get("provider_id"),
        selected_rule.get("pattern_match_type"),
        selected_rule.get("content_match_type"),
        _short_text(str(selected_rule.get("pattern") or ""), 120),
        _short_text(str(selected_rule.get("content_pattern") or ""), 120),
        selected_rank,
    )
    try:
        reply_text = await _call_provider(selected_rule, inbound_text)
        await _send_worktool_text(robot_id, scene, req, reply_text)
        _insert_message_log(robot_pk, "outbound", scene, reply_text, "success")
        _update_qa_monitor_log(local_log_id, reply_text, "success", time.perf_counter() - started_at)
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
                _update_qa_monitor_log(local_log_id, default_reply, "success", time.perf_counter() - started_at)
            except Exception as e2:
                logger.exception("qa_callback_fallback_send_failed robot_id=%s scene=%s err=%s", robot_id, scene, str(e2))
                _insert_message_log(robot_pk, "outbound", scene, str(e2), "failed")
                _update_qa_monitor_log(local_log_id, str(e2), "failed", time.perf_counter() - started_at)
            return QAResponse(code=0, message="参数接收成功")
        _update_qa_monitor_log(local_log_id, str(e), "failed", time.perf_counter() - started_at)
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


@app.get("/api/v1/message-monitor/logs")
async def get_message_monitor_logs(
    robot_id: str,
    page: int = 1,
    size: int = 20,
    sort: str = "start_time,desc",
    name: Optional[str] = None,
    scene: str = "all",
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    robot = _require_robot_access(int(user["id"]), robot_id)
    scene = (scene or "all").strip().lower()
    if scene not in {"all", "group", "private"}:
        raise HTTPException(status_code=400, detail="scene must be all/group/private")
    kw = (name or "").strip()
    use_local = await _is_platform_message_callback(robot_id)
    if not use_local:
        res = await fetch_worktool_api(
            "/robot/qaLog/list",
            {"robotId": robot_id, "page": page, "size": size, "sort": sort, "name": kw or None},
        )
        data = (res.get("data") if isinstance(res, dict) else None) or {}
        rows = data.get("list") or []
        if not isinstance(rows, list):
            rows = []
        filtered: List[Dict[str, Any]] = []
        kw_lower = kw.lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            room_type = int(row.get("roomType") or 0)
            if scene == "group" and room_type not in {1, 3}:
                continue
            if scene == "private" and room_type not in {2, 4}:
                continue
            if kw:
                group_name = str(row.get("groupName") or "")
                received_name = str(row.get("receivedName") or "")
                if kw_lower not in group_name.lower() and kw_lower not in received_name.lower():
                    continue
            filtered.append(row)
        data["list"] = filtered
        data["total"] = len(filtered)
        data["pageNum"] = page
        data["pageSize"] = size
        return {"source": "worktool", "data": data}

    conn = db_conn()
    try:
        with conn.cursor() as cur:
            where_parts = ["q.robot_pk=%s"]
            params: List[Any] = [int(robot["id"])]
            if scene == "group":
                where_parts.append("q.room_type IN (1,3)")
            elif scene == "private":
                where_parts.append("q.room_type IN (2,4)")
            if kw:
                like_kw = f"%{kw}%"
                where_parts.append("(q.group_name LIKE %s OR q.received_name LIKE %s)")
                params.extend([like_kw, like_kw])
            where_sql = " AND ".join(where_parts)
            cur.execute(f"SELECT COUNT(1) AS c FROM qa_monitor_logs q WHERE {where_sql}", tuple(params))
            total = int((cur.fetchone() or {}).get("c") or 0)
            offset = (page - 1) * size
            cur.execute(
                f"""
                SELECT q.id,q.room_type,q.text_type,q.at_me,q.group_name,q.received_name,q.question,q.answer,q.message_id,q.callback_url,q.time_cost,q.created_at
                FROM qa_monitor_logs q
                WHERE {where_sql}
                ORDER BY q.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [size, offset]),
            )
            rows = cur.fetchall() or []
            items: List[Dict[str, Any]] = []
            for row in rows:
                created_at = row.get("created_at")
                start_time = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or "")
                items.append(
                    {
                        "robotId": robot_id,
                        "startTime": start_time,
                        "timeCost": float(row.get("time_cost") or 0),
                        "groupName": row.get("group_name") or "",
                        "receivedName": row.get("received_name") or "",
                        "roomType": int(row.get("room_type") or 0),
                        "textType": int(row.get("text_type") or 1),
                        "openThirdParty": 1,
                        "url": row.get("callback_url") or build_robot_callback_url(robot_id),
                        "rawSpoken": row.get("question") or "",
                        "question": row.get("question") or "",
                        "answer": row.get("answer") or "",
                        "messageId": row.get("message_id") or f"local-{row.get('id')}",
                        "atMe": bool(row.get("at_me")),
                    }
                )
            return {
                "source": "local",
                "data": {"list": items, "total": total, "pageNum": page, "pageSize": size},
            }
    finally:
        conn.close()


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
    res = await bind_callback_by_type(rid, (body.callback_url or "").strip(), int(body.type))
    return {"ok": True, "type": body.type, "result": res}


@app.post("/api/v1/robot-info/callbacks/delete-by-type")
async def delete_robot_callback(body: RobotCallbackDeletePayload, user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    rid = (body.robot_id or "").strip()
    _require_robot_access(int(user["id"]), rid)
    result = await delete_callback_by_type(rid, int(body.type))
    return {"ok": True, "robot_id": rid, "type": int(body.type), "result": result}


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

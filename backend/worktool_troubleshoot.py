import json
import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import aiohttp
import pymysql
from fastapi import HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.troubleshoot")


class TroubleshootSearchPayload(BaseModel):
    robot_id: str = ""
    message_id: str = ""
    keyword: str = ""
    start_time: str = ""
    end_time: str = ""
    limit: int = Field(default=20, ge=1, le=100)


def _safe_limit(limit: int, default: int = 20, max_limit: int = 100) -> int:
    if limit <= 0:
        return default
    return min(limit, max_limit)


def _mask_ip(ip: Optional[str]) -> str:
    if not ip:
        return "-"
    chunks = ip.split(".")
    if len(chunks) == 4:
        return f"{chunks[0]}.{chunks[1]}.*.*"
    return ip


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


def _get_worktool_mysql_config() -> Optional[Dict[str, Any]]:
    host = (os.getenv("WORKTOOL_DB_HOST") or os.getenv("DB_HOST") or "").strip()
    port_raw = (os.getenv("WORKTOOL_DB_PORT") or os.getenv("DB_PORT") or "3306").strip()
    user = (os.getenv("WORKTOOL_DB_USER") or os.getenv("DB_USER") or "").strip()
    password = os.getenv("WORKTOOL_DB_PASSWORD") or os.getenv("DB_PASSWORD") or ""
    database = (os.getenv("WORKTOOL_DB_NAME") or os.getenv("DB_NAME") or "").strip()
    if not (host and user and database):
        return None
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 5,
        "read_timeout": 15,
        "write_timeout": 15,
    }


def _worktool_mysql_query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cfg = _get_worktool_mysql_config()
    if not cfg:
        return []
    try:
        conn = pymysql.connect(**cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return list(cur.fetchall() or [])
        finally:
            conn.close()
    except Exception as e:
        logger.warning("worktool mysql query failed: %s", e)
        return []


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
        rows = _worktool_mysql_query(sql, params)
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
        for field_name in keyword_fields:
            chunks.append(f"{field_name} LIKE %s")
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
    return _worktool_mysql_query(sql, params)


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
        if fail_list:
            return False
        success_list = parse_json_list(row.get("success_list"))
        if success_list:
            return True
        if (row.get("error_reason") or "").strip():
            return False
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
            "执行耗时(秒)": x.get("time_cost"),
            "失败原因": (x.get("error_reason") or "")[:200],
        }
        for x in rows
    ]


async def _fetch_worktool_api_loose(api_base: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{api_base.rstrip('/')}{path}"
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"worktool request failed: status={resp.status}")
            return data if isinstance(data, dict) else {"data": data}


async def _fetch_qa_logs_page(
    fetch_worktool_api: Callable[[str, Dict[str, Any]], Any],
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


def _sanitize_qa_rows(rows: List[Dict[str, Any]], message_id: str, limit: int) -> List[Dict[str, Any]]:
    filtered = rows
    if message_id:
        filtered = [x for x in rows if (x.get("messageId") or "") == message_id]
    return [
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
        for x in filtered[:limit]
    ]


def _load_local_robot_ids(db_conn_factory: Callable[[], Any]) -> List[str]:
    conn = db_conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT robot_id FROM robots ORDER BY id ASC")
            rows = cur.fetchall() or []
            return [str(x["robot_id"]) for x in rows if x.get("robot_id")]
    finally:
        conn.close()


def _load_local_message_logs(db_conn_factory: Callable[[], Any], robot_id: str, limit: int) -> List[Dict[str, Any]]:
    conn = db_conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ml.direction,ml.scene,ml.normalized_content,ml.status,ml.created_at
                FROM message_logs ml
                JOIN robots r ON r.id=ml.robot_pk
                WHERE r.robot_id=%s
                ORDER BY ml.id DESC
                LIMIT %s
                """,
                (robot_id, limit),
            )
            return list(cur.fetchall() or [])
    finally:
        conn.close()


async def _resolve_robot_id_by_message_via_api(
    get_worktool_api_base: Callable[[], str],
    robot_ids: List[str],
    message_id: str,
) -> Optional[str]:
    page_size = 50
    max_scan_pages = 2
    api_base = get_worktool_api_base()

    async def has_msg_in_raw_message(robot_id: str) -> bool:
        for page in range(1, max_scan_pages + 1):
            data = await _fetch_worktool_api_loose(
                api_base,
                "/wework/listRawMessage",
                {"robotId": robot_id, "page": page, "size": page_size, "messageId": message_id},
            )
            payload = data.get("data", {})
            rows = payload.get("list") or payload.get("records") or payload or []
            if not isinstance(rows, list):
                rows = []
            if any((x.get("messageId") or "") == message_id for x in rows):
                return True
            total_page = int(payload.get("totalPage") or 0) if isinstance(payload, dict) else 0
            if not rows or (total_page > 0 and page >= total_page):
                return False
        return False

    async def has_msg_in_raw_confirm(robot_id: str) -> bool:
        for page in range(1, max_scan_pages + 1):
            data = await _fetch_worktool_api_loose(
                api_base,
                "/robot/rawMsg/list",
                {"robotId": robot_id, "page": page, "size": page_size, "messageId": message_id},
            )
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
            if not rows or (total_page > 0 and page >= total_page):
                return False
        return False

    for robot_id in robot_ids:
        try:
            if await has_msg_in_raw_message(robot_id):
                return robot_id
            if await has_msg_in_raw_confirm(robot_id):
                return robot_id
        except Exception:
            continue
    return None


async def _fetch_raw_message_records(
    get_worktool_api_base: Callable[[], str], robot_id: str, message_id: str, limit: int
) -> List[Dict[str, Any]]:
    data = await _fetch_worktool_api_loose(
        get_worktool_api_base(),
        "/wework/listRawMessage",
        {"robotId": robot_id, "page": 1, "size": min(max(limit, 20), 200)},
    )
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


async def _fetch_raw_msg_confirms(
    get_worktool_api_base: Callable[[], str], robot_id: str, message_id: str, limit: int
) -> List[Dict[str, Any]]:
    data = await _fetch_worktool_api_loose(
        get_worktool_api_base(),
        "/robot/rawMsg/list",
        {"robotId": robot_id, "size": min(max(limit, 20), 200)},
    )
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


async def run_troubleshoot_search(
    body: TroubleshootSearchPayload,
    *,
    enable_troubleshoot: bool,
    get_worktool_api_base: Callable[[], str],
    fetch_worktool_api: Callable[[str, Dict[str, Any]], Any],
    db_conn_factory: Callable[[], Any],
) -> Dict[str, Any]:
    if not enable_troubleshoot:
        raise HTTPException(status_code=404, detail="机器人排查功能在开源版中默认关闭")

    robot_id = (body.robot_id or "").strip()
    message_id = (body.message_id or "").strip()
    keyword = (body.keyword or "").strip()
    start_time = (body.start_time or "").strip()
    end_time = (body.end_time or "").strip()
    limit = _safe_limit(body.limit)

    if not robot_id and not message_id:
        raise HTTPException(status_code=400, detail="robot_id 和 message_id 至少填写一个")

    robot_ids = _load_local_robot_ids(db_conn_factory)
    resolved_from_message = False
    if not robot_id and message_id:
        robot_id = _mysql_resolve_robot_id_by_message(message_id) or ""
        if not robot_id:
            robot_id = await _resolve_robot_id_by_message_via_api(get_worktool_api_base, robot_ids, message_id) or ""
        resolved_from_message = bool(robot_id)

    if not robot_id:
        return {
            "input": {
                "robot_id": body.robot_id,
                "message_id": message_id,
                "keyword": keyword,
                "start_time": start_time,
                "end_time": end_time,
                "limit": limit,
            },
            "resolved": {"robot_id": "", "message_resolved_robot": False},
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
                f"未通过 message_id 反查到机器人（已扫描当前系统内 {len(robot_ids)} 个机器人）。",
                "请补充 robot_id 后重试，可直接定位该消息所在机器人。",
            ],
        }

    detail_data = (await fetch_worktool_api("/robot/robotInfo/get-detail", {"robotId": robot_id})).get("data", {}) or {}
    callbacks_data = (await fetch_worktool_api("/robot/robotInfo/callBack/get", {"robotId": robot_id})).get("data", []) or []
    online_data = (await fetch_worktool_api("/robot/robotInfo/online", {"robotId": robot_id})).get("data", False)
    online_infos_data = (await fetch_worktool_api("/robot/robotInfo/onlineInfos", {"robotId": robot_id})).get("data", []) or []
    online_infos_data = sorted(
        online_infos_data,
        key=lambda x: _parse_loose_datetime(x.get("onlineTime")),
        reverse=True,
    )[:20]
    qa_rows = await _fetch_qa_logs_page(
        fetch_worktool_api=fetch_worktool_api,
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
        else await _fetch_raw_message_records(get_worktool_api_base, robot_id, message_id, limit)
    )
    raw_confirm_rows = (
        _sanitize_mysql_raw_confirm_rows(mysql_raw_confirm_rows)
        if mysql_raw_confirm_rows
        else await _fetch_raw_msg_confirms(get_worktool_api_base, robot_id, message_id, limit)
    )

    logs = _load_local_message_logs(db_conn_factory, robot_id, limit)

    diagnostics: List[str] = []
    if message_id and not any((x.get("messageId") or "") == message_id for x in qa_rows):
        diagnostics.append("该 message_id 在近1页问答记录中未命中，请检查时间范围后重试。")
    if not online_data:
        diagnostics.append("机器人当前离线。")
    if detail_data.get("robotType") == 4:
        diagnostics.append("机器人状态无效（robotType=4）。")
    if message_id and raw_message_rows and not raw_confirm_rows:
        diagnostics.append("找到指令发送记录，但未找到客户端执行结果记录。")
    if not _get_worktool_mysql_config():
        diagnostics.append("未配置 WorkTool MySQL，只能使用 WorkTool 接口做排查。")

    return {
        "input": {
            "robot_id": body.robot_id,
            "message_id": message_id,
            "keyword": keyword,
            "start_time": start_time,
            "end_time": end_time,
            "limit": limit,
        },
        "resolved": {"robot_id": robot_id, "message_resolved_robot": resolved_from_message},
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
                    "时间": str(r.get("created_at") or ""),
                    "方向": "接收" if r.get("direction") == "inbound" else "发送",
                    "场景": "群聊" if r.get("scene") == "group" else "私聊",
                    "会话": "-",
                    "消息": (r.get("normalized_content") or "")[:120],
                    "状态": r.get("status"),
                }
                for r in logs
            ],
        },
        "diagnostics": diagnostics,
    }

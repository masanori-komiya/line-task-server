import base64
import hashlib
import hmac
import os
from typing import Any, Dict, Optional

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.line_api import build_tasks_flex, fetch_line_profile, reply_message

# ✅ LINE側が /line/webhook に投げてるので prefix を /line にする
router = APIRouter(prefix="/line")


# ==========================
# LINE署名検証
# ==========================
def verify_line_signature(body: bytes, x_line_signature: Optional[str]) -> None:
    secret = os.getenv("LINE_CHANNEL_SECRET", "").strip()
    if not secret:
        return
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing x-line-signature")
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    if not hmac.compare_digest(expected, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")


# ==========================
# コマンド判定
# ==========================
def parse_rerun_command(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    if not t.endswith("再実行"):
        return None
    name = t[: -len("再実行")].strip()
    return name or None


def is_tasks_command(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"tasks", "task", "タスク", "たすく"}


# ==========================
# DB操作
# ==========================
async def upsert_user_from_profile(conn: asyncpg.Connection, user_id: str, profile: Dict[str, Any]) -> None:
    sql = """
    INSERT INTO users (user_id, user_name, picture_url, status_message, last_seen_at)
    VALUES ($1, $2, $3, $4, NOW())
    ON CONFLICT (user_id)
    DO UPDATE SET
        user_name=EXCLUDED.user_name,
        picture_url=EXCLUDED.picture_url,
        status_message=EXCLUDED.status_message,
        last_seen_at=NOW()
    """
    await conn.execute(
        sql,
        user_id,
        profile.get("displayName"),
        profile.get("pictureUrl"),
        profile.get("statusMessage"),
    )


async def enqueue_rerun(pool: asyncpg.Pool, user_id: str, task_name: str, requested_by: Optional[str]) -> Dict[str, Any]:
    # ✅ 全角スペース/連続空白を正規化して比較（"通勤バス　乗車記録" 対策）
    sql_find = r"""
    SELECT task_id, pc_name, enabled
    FROM tasks
    WHERE user_id=$1
      AND regexp_replace(translate(name, '　', ' '), '\s+', ' ', 'g')
          = regexp_replace(translate($2,  '　', ' '), '\s+', ' ', 'g')
    ORDER BY created_at DESC
    LIMIT 1
    """

    sql_insert = """
    INSERT INTO task_rerun_queue (task_id, user_id, pc_name, requested_by, status)
    VALUES ($1, $2, $3, $4, 'queued')
    ON CONFLICT DO NOTHING
    RETURNING request_id
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql_find, user_id, task_name)
        if not row:
            return {"ok": False, "reason": "not_found"}

        if not bool(row["enabled"]):
            return {"ok": False, "reason": "disabled"}

        request_id = await conn.fetchval(sql_insert, row["task_id"], user_id, row["pc_name"], requested_by)
        if not request_id:
            return {"ok": False, "reason": "already_pending"}

        return {
            "ok": True,
            "request_id": str(request_id),
            "task_id": str(row["task_id"]),
            "pc_name": row["pc_name"],
        }


async def fetch_tasks_for_user(pool: asyncpg.Pool, user_id: str) -> list[dict]:
    sql = """
    SELECT name, schedule_value, plan_tag, expires_at, enabled
    FROM tasks
    WHERE user_id=$1
    ORDER BY created_at DESC
    LIMIT 50
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, user_id)
    return [dict(r) for r in rows]


# ==========================
# Webhook endpoint
# ==========================
@router.post("/webhook")  # ✅ /line/webhook
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
) -> JSONResponse:
    body = await request.body()
    verify_line_signature(body, x_line_signature)

    payload = await request.json()
    events = payload.get("events") or []

    # ✅ main.py は app.state.db_pool
    pool: asyncpg.Pool = request.app.state.db_pool

    for ev in events:
        if ev.get("type") != "message":
            continue

        message = ev.get("message") or {}
        if message.get("type") != "text":
            continue

        text = message.get("text") or ""
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")
        if not reply_token or not user_id:
            continue

        profile = await fetch_line_profile(user_id)
        display_name = profile.get("displayName") or "user"

        async with pool.acquire() as conn:
            await upsert_user_from_profile(conn, user_id, profile)

        if is_tasks_command(text):
            tasks = await fetch_tasks_for_user(pool, user_id)
            flex = build_tasks_flex(display_name, tasks)
            await reply_message(reply_token, [flex])
            continue

        task_name = parse_rerun_command(text)
        if task_name:
            result = await enqueue_rerun(pool, user_id, task_name, requested_by=display_name)

            if result["ok"]:
                msg = f"OK！「{task_name}」を再実行キューに追加しました。\n（元の実行PC: {result['pc_name']}）"
            else:
                reason = result.get("reason")
                if reason == "not_found":
                    msg = f"「{task_name}」が見つかりませんでした。"
                elif reason == "disabled":
                    msg = f"「{task_name}」は disabled です（有効化してから再実行してね）。"
                elif reason == "already_pending":
                    msg = f"「{task_name}」はすでに再実行待ち/実行中です。終わってからもう一度送ってね。"
                else:
                    msg = "再実行の追加に失敗しました。"

            await reply_message(reply_token, [{"type": "text", "text": msg}])
            continue

        await reply_message(reply_token, [{"type": "text", "text": "コマンド例：\n・tasks\n・<タスク名> 再実行"}])

    return JSONResponse({"ok": True})


# ✅ 互換用：もしLINE側URLを /webhook にしていた場合でも受けられる
# （prefix="/line" を使っているので、これは /webhook を追加するための別ルーターが必要）
legacy_router = APIRouter()

@legacy_router.post("/webhook")
async def legacy_webhook(request: Request, x_line_signature: Optional[str] = Header(default=None)):
    return await line_webhook(request, x_line_signature)

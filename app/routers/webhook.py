import base64
import hashlib
import hmac
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.line_api import build_tasks_flex, fetch_line_profile, reply_message

router = APIRouter()

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

def is_tasks_command(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"タスク", "タスク一覧", "tasks", "task"}

async def upsert_user(pool, user_id: str, event_type: str) -> Dict[str, Any]:
    profile = await fetch_line_profile(user_id)
    user_name = profile.get("displayName") or "unknown"
    picture_url = profile.get("pictureUrl")
    status_message = profile.get("statusMessage")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, user_name, picture_url, status_message, last_event, last_seen_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
              user_name=EXCLUDED.user_name,
              picture_url=EXCLUDED.picture_url,
              status_message=EXCLUDED.status_message,
              last_event=EXCLUDED.last_event,
              last_seen_at=NOW()
            """,
            user_id, user_name, picture_url, status_message, event_type
        )
    return {"user_id": user_id, "user_name": user_name}

async def fetch_tasks_for_user(pool, user_id: str):
    if not user_id:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT name, schedule_value, plan_tag, expires_at, enabled
            FROM tasks
            WHERE user_id = $1
            ORDER BY created_at DESC
            """,
            user_id,
        )

    return [dict(r) for r in rows]


async def line_webhook(request: Request, x_line_signature: Optional[str] = Header(default=None)):
    body = await request.body()
    verify_line_signature(body, x_line_signature)

    data = await request.json()
    events = data.get("events", [])
    pool = request.app.state.db_pool

    for ev in events:
        event_type = ev.get("type") or "unknown"
        src = ev.get("source", {})
        user_id = src.get("userId")
        if not user_id:
            continue

        user_info = await upsert_user(pool, user_id, event_type)

        if event_type == "message":
            msg = ev.get("message", {})
            if msg.get("type") == "text":
                text = msg.get("text", "")
                if is_tasks_command(text):
                    reply_token = ev.get("replyToken")
                    if reply_token:
                        tasks = await fetch_tasks_for_user(pool, user_id)
                        flex = build_tasks_flex(user_info.get("user_name", ""), tasks)
                        await reply_message(reply_token, [flex])

    return JSONResponse({"ok": True, "received": len(events)})

@router.get("/line/webhook")
def line_webhook_get():
    return {"ok": True}

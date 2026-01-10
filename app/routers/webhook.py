from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.line_api import verify_line_signature, fetch_line_profile

router = APIRouter(prefix="/line")


@router.post("/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
):
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

        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1", user_id)

        profile = {}
        if not exists:
            profile = await fetch_line_profile(user_id)

        now = datetime.now(timezone.utc)

        sql = """
        INSERT INTO users (user_id, user_name, picture_url, status_message, last_event, last_seen_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (user_id) DO UPDATE SET
            last_event   = EXCLUDED.last_event,
            last_seen_at = EXCLUDED.last_seen_at
        ;
        """
        async with pool.acquire() as conn:
            await conn.execute(
                sql,
                user_id,
                profile.get("displayName") if profile else None,
                profile.get("pictureUrl") if profile else None,
                profile.get("statusMessage") if profile else None,
                event_type,
                now,
            )

    return JSONResponse({"ok": True, "received": len(events)})


@router.get("/webhook")
async def line_webhook_get():
    return {"ok": True}

import os
import hmac
import hashlib
import base64
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

LINE_PROFILE_API = "https://api.line.me/v2/bot/profile/{}"


def verify_line_signature(body: bytes, x_line_signature: Optional[str]) -> None:
    secret = os.getenv("LINE_CHANNEL_SECRET", "")
    if not secret:
        # ローカル検証のため未設定ならスキップ可
        return

    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing x-line-signature")

    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")

    if not hmac.compare_digest(expected, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")


async def fetch_line_profile(user_id: str) -> Dict[str, Any]:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return {}

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(LINE_PROFILE_API.format(user_id), headers=headers, timeout=7)
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}

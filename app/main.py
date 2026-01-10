import os
import hmac
import hashlib
import base64
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

# まずはDBなしでメモリ保存（再起動すると消える）
# 例: [{"userId": "...", "event": "follow", "time": "..."}]
SEEN_USERS: List[Dict[str, Any]] = []


def verify_line_signature(body: bytes, x_line_signature: Optional[str]) -> None:
    """
    LINEの署名検証。
    LINE_CHANNEL_SECRET が未設定ならローカル動作のため検証をスキップ。
    """
    secret = os.getenv("LINE_CHANNEL_SECRET", "")
    if not secret:
        # ローカル確認用（本番は必ず設定）
        return

    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing x-line-signature")

    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")

    if not hmac.compare_digest(expected, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")


def record_user(user_id: str, event_type: str) -> None:
    # 重複は1回にしたいならここで弾く
    for u in SEEN_USERS:
        if u["userId"] == user_id:
            return
    SEEN_USERS.append(
        {
            "userId": user_id,
            "event": event_type,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><body style="font-family: -apple-system, sans-serif; padding:24px;">
      <h1>✅ LINE Task Server is running</h1>
      <ul>
        <li><a href="/health">/health</a></li>
        <li><a href="/admin/users">/admin/users</a></li>
      </ul>
    </body></html>
    """


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users():
    rows = ""
    for u in SEEN_USERS:
        rows += f"<tr><td>{u['time']}</td><td>{u['event']}</td><td style='font-family: ui-monospace, SFMono-Regular;'>{u['userId']}</td></tr>"

    return f"""
    <html><body style="font-family: -apple-system, sans-serif; padding:24px;">
      <h1>Users</h1>
      <p>取得済み userId（※今はメモリ保存。再起動すると消えます）</p>
      <table border="1" cellpadding="8" cellspacing="0">
        <thead><tr><th>time</th><th>event</th><th>userId</th></tr></thead>
        <tbody>{rows if rows else "<tr><td colspan='3'>まだ0件</td></tr>"}</tbody>
      </table>
      <p style="margin-top:16px;">
        Webhook URL: <code>/line/webhook</code>
      </p>
    </body></html>
    """


@app.post("/line/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
):
    body = await request.body()
    verify_line_signature(body, x_line_signature)

    data = await request.json()
    events = data.get("events", [])

    # 受け取ったイベントを眺めたい時にログ出し（必要なら）
    # print(data)

    for ev in events:
        event_type = ev.get("type")
        src = ev.get("source", {})
        user_id = src.get("userId")
        if user_id:
            record_user(user_id, event_type or "unknown")

    return JSONResponse({"ok": True, "received": len(events)})


# LINEの「Verify」用途に GET が飛んでくる場合に備えて（保険）
@app.get("/line/webhook")
def line_webhook_get():
    return {"ok": True}

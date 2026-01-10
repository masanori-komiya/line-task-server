import os
import hmac
import hashlib
import base64
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

# =========================================================
# メモリ保存（再起動で消える）
# 例: [{"userId": "...", "userName": "...", "pictureUrl": "...", "event": "...", "time": "..."}]
# =========================================================
SEEN_USERS: List[Dict[str, Any]] = []

LINE_PROFILE_API = "https://api.line.me/v2/bot/profile/{}"


# =========================================================
# LINE署名検証
# =========================================================
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


# =========================================================
# LINEプロフィール取得
# =========================================================
async def fetch_line_profile(user_id: str) -> Dict[str, Any]:
    """
    Messaging APIのプロフィール取得 API を叩いて displayName 等を取得。
    必要: LINE_CHANNEL_ACCESS_TOKEN
    """
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return {}

    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                LINE_PROFILE_API.format(user_id),
                headers=headers,
                timeout=7,
            )
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}


# =========================================================
# ユーザー保存（重複は弾く）
# =========================================================
async def record_user(user_id: str, event_type: str) -> None:
    for u in SEEN_USERS:
        if u["userId"] == user_id:
            # 既に居る場合は「最終イベント/時刻」だけ更新したいならここで更新してもOK
            u["event"] = event_type
            u["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return

    profile = await fetch_line_profile(user_id)
    SEEN_USERS.append(
        {
            "userId": user_id,
            "userName": profile.get("displayName", "unknown"),
            "pictureUrl": profile.get("pictureUrl"),
            "statusMessage": profile.get("statusMessage"),
            "event": event_type,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


# =========================================================
# 画面
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><body style="font-family: -apple-system, sans-serif; padding:24px;">
      <h1>✅ LINE Task Server is running</h1>
      <ul>
        <li><a href="/health">/health</a></li>
        <li><a href="/admin/users">/admin/users</a></li>
      </ul>

      <h3>Env checklist</h3>
      <ul>
        <li>LINE_CHANNEL_SECRET: Webhook署名検証（未設定なら検証スキップ）</li>
        <li>LINE_CHANNEL_ACCESS_TOKEN: ユーザープロフィール取得に必須</li>
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
        pic = u.get("pictureUrl")
        pic_html = (
            f"<img src='{pic}' width='40' height='40' style='border-radius:50%;' />"
            if pic
            else ""
        )
        rows += (
            "<tr>"
            f"<td>{u.get('time','')}</td>"
            f"<td>{u.get('event','')}</td>"
            f"<td>{pic_html}</td>"
            f"<td>{u.get('userName','')}</td>"
            f"<td style='font-family: ui-monospace, SFMono-Regular;'>{u.get('userId','')}</td>"
            "</tr>"
        )

    return f"""
    <html><body style="font-family: -apple-system, sans-serif; padding:24px;">
      <h1>Users</h1>
      <p>取得済み userId / userName（※今はメモリ保存。再起動すると消えます）</p>

      <table border="1" cellpadding="8" cellspacing="0">
        <thead>
          <tr>
            <th>time</th>
            <th>event</th>
            <th>icon</th>
            <th>name</th>
            <th>userId</th>
          </tr>
        </thead>
        <tbody>{rows if rows else "<tr><td colspan='5'>まだ0件</td></tr>"}</tbody>
      </table>

      <p style="margin-top:16px;">
        Webhook URL: <code>/line/webhook</code>
      </p>

      <h3>Tips</h3>
      <ul>
        <li>ユーザー名を取るには <code>LINE_CHANNEL_ACCESS_TOKEN</code> が必要</li>
        <li>ブロックされている/条件によってプロフィール取得が失敗することがあります（unknown表示）</li>
      </ul>
    </body></html>
    """


# =========================================================
# Webhook
# =========================================================
@app.post("/line/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
):
    body = await request.body()
    verify_line_signature(body, x_line_signature)

    data = await request.json()
    print("LINE webhook received:", data)

    events = data.get("events", [])
    for ev in events:
        event_type = ev.get("type") or "unknown"
        src = ev.get("source", {})
        user_id = src.get("userId")
        if user_id:
            await record_user(user_id, event_type)

    return JSONResponse({"ok": True, "received": len(events)})


# LINEの「Verify」用途に GET が飛んでくる場合に備えて（保険）
@app.get("/line/webhook")
def line_webhook_get():
    return {"ok": True}

import os
from typing import Any, Dict, List

import httpx

LINE_PROFILE_API = "https://api.line.me/v2/bot/profile/{}"
LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"

def _token() -> str:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

async def fetch_line_profile(user_id: str) -> Dict[str, Any]:
    token = _token()
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

async def reply_message(reply_token: str, messages: List[Dict[str, Any]]) -> bool:
    token = _token()
    if not token:
        return False

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"replyToken": reply_token, "messages": messages}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(LINE_REPLY_API, headers=headers, json=payload, timeout=10)
        if r.status_code != 200:
            print("LINE reply failed:", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print("LINE reply exception:", repr(e))
        return False

def build_tasks_flex(user_name: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    表示内容：タスク名 / 実行時間 / 期限 / free or paid（この順番）
    """
    header_title = f"{user_name} のタスク" if user_name else "タスク一覧"

    if not tasks:
        return {
            "type": "flex",
            "altText": "タスク一覧（0件）",
            "contents": {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "contents": [
                        {"type": "text", "text": header_title, "weight": "bold", "size": "lg"},
                        {"type": "text", "text": "タスクがまだありません。", "wrap": True, "color": "#666666"},
                    ],
                },
            },
        }

    bubbles: List[Dict[str, Any]] = []
    for t in tasks[:10]:
        name = t.get("name") or "(no name)"
        time = t.get("schedule_value") or "-"
        plan = (t.get("plan_tag") or "free").lower()

        expires = t.get("expires_at")
        if expires:
            try:
                expires_text = expires.strftime("%Y-%m-%d")
            except Exception:
                expires_text = str(expires)
        else:
            expires_text = "-"

        bubbles.append(
            {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                        {"type": "text", "text": name, "weight": "bold", "size": "md", "wrap": True},
                        {"type": "text", "text": f"🕒 実行時間: {time}", "size": "sm", "color": "#444444"},
                        {"type": "text", "text": f"⏳ 期限: {expires_text}", "size": "sm", "color": "#444444"},
                        {"type": "text", "text": f"🏷 {plan}", "size": "sm", "color": "#444444"},
                    ],
                },
            }
        )

    return {
        "type": "flex",
        "altText": f"タスク一覧（{len(tasks)}件）",
        "contents": {"type": "carousel", "contents": bubbles},
    }

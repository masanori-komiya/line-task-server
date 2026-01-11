import os
from datetime import datetime
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


def _format_yy_mm_dd(value) -> str:
    if not value:
        return "-"
    try:
        if isinstance(value, datetime):
            return value.strftime("%y/%m/%d")
        s = str(value)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%y/%m/%d")
        except Exception:
            return s[:10].replace("-", "/")[2:]
    except Exception:
        return "-"


def build_tasks_flex(user_name: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    contents: List[Dict[str, Any]] = [
        {"type": "text", "text": f"{len(tasks)} 件", "size": "sm", "color": "#666666"},
        {"type": "separator", "margin": "md"},
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "margin": "sm",
            "contents": [
                {"type": "text", "text": "タスク名", "size": "xxs", "weight": "bold", "flex": 6, "align": "center", "color": "#111111"},
                {"type": "text", "text": "実行時間", "size": "xxs", "weight": "bold", "flex": 3, "align": "center", "color": "#111111"},
                {"type": "text", "text": "期限",     "size": "xxs", "weight": "bold", "flex": 3, "align": "center", "color": "#111111"},
                {"type": "text", "text": "プラン",   "size": "xxs", "weight": "bold", "flex": 2, "align": "center", "color": "#111111"},
            ],
        },
        {"type": "separator", "margin": "sm"},
    ]

    if not tasks:
        contents.append({"type": "text", "text": "タスクがありません。", "size": "sm", "color": "#666666", "margin": "md", "wrap": True})
    else:
        for t in tasks[:20]:
            name = t.get("name") or "-"
            time = t.get("schedule_value") or "-"
            plan = (t.get("plan_tag") or "free").lower()
            enabled = bool(t.get("enabled", True))

            expires_text = _format_yy_mm_dd(t.get("expires_at"))

            is_gray = not enabled
            row_color = "#AAAAAA" if is_gray else "#222222"
            plan_color = "#AAAAAA" if is_gray else ("#B42318" if plan == "paid" else "#1A7F37")
            status_suffix = "（disabled）" if is_gray else ""

            contents.append(
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "margin": "sm",
                    "contents": [
                        {"type": "text", "text": f"{name}{status_suffix}", "size": "xxs", "wrap": True, "flex": 6, "color": row_color},
                        {"type": "text", "text": time,         "size": "xxs", "flex": 3, "align": "center", "color": row_color},
                        {"type": "text", "text": expires_text, "size": "xxs", "flex": 3, "align": "center", "color": row_color},
                        {"type": "text", "text": plan,         "size": "xxs", "flex": 2, "align": "center", "color": plan_color},
                    ],
                }
            )

        if len(tasks) > 20:
            contents.extend(
                [
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": f"※ 表示は先頭20件まで（全 {len(tasks)} 件）", "size": "xs", "color": "#666666", "wrap": True, "margin": "sm"},
                ]
            )

    return {
        "type": "flex",
        "altText": f"実行中のタスク（{len(tasks)}件）",
        "contents": {
            "type": "bubble",
            "styles": {"body": {"backgroundColor": "#FFFFFF"}},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": contents},
        },
    }

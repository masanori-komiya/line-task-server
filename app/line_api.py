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
    1つのBubbleでテーブル風表示（mobile最適化）
    表示カラム：タスク名 / 実行時間 / 有効期限 / プラン
    ※ tasks は enabledのみが来る前提（webhook側で絞る）
    """
    title = "実行中のタスク"

    # ヘッダー行（列幅はmobile最適化：名前を広め、他は狭め）
    header_row = {
        "type": "box",
        "layout": "horizontal",
        "margin": "md",
        "contents": [
            {"type": "text", "text": "タスク名", "size": "xs", "weight": "bold", "flex": 6, "color": "#111111"},
            {"type": "text", "text": "時間",     "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": "#111111"},
            {"type": "text", "text": "期限",     "size": "xs", "weight": "bold", "flex": 3, "align": "end", "color": "#111111"},
            {"type": "text", "text": "プラン",   "size": "xs", "weight": "bold", "flex": 2, "align": "end", "color": "#111111"},
        ],
    }

    contents: List[Dict[str, Any]] = [
        {"type": "text", "text": title, "weight": "bold", "size": "lg", "wrap": True},
        {"type": "text", "text": f"{len(tasks)} 件", "size": "sm", "color": "#666666"},
        {"type": "separator", "margin": "md"},
        header_row,
        {"type": "separator", "margin": "sm"},
    ]

    if not tasks:
        contents.append(
            {"type": "text", "text": "実行中のタスクはありません。", "size": "sm", "color": "#666666", "margin": "md", "wrap": True}
        )
    else:
        # データ行：最大20件
        for t in tasks[:20]:
            name = t.get("name") or "-"
            time = t.get("schedule_value") or "-"
            plan = (t.get("plan_tag") or "free").lower()

            expires = t.get("expires_at")
            if expires:
                try:
                    expires_text = expires.strftime("%m/%d")  # mobile向けに短縮（例: 01/31）
                except Exception:
                    expires_text = str(expires)[:5]
            else:
                expires_text = "-"

            # planをチップ風に見せる（テキストだけで）
            plan_text = "paid" if plan == "paid" else "free"
            plan_color = "#B42318" if plan == "paid" else "#1A7F37"  # paidは赤寄り、freeは緑寄り

            contents.append(
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "margin": "sm",
                    "contents": [
                        {
                            "type": "text",
                            "text": name,
                            "size": "sm",
                            "wrap": True,
                            "flex": 6,
                            "color": "#222222",
                        },
                        {
                            "type": "text",
                            "text": time,
                            "size": "sm",
                            "flex": 2,
                            "align": "end",
                            "color": "#222222",
                        },
                        {
                            "type": "text",
                            "text": expires_text,
                            "size": "sm",
                            "flex": 3,
                            "align": "end",
                            "color": "#222222",
                        },
                        {
                            "type": "text",
                            "text": plan_text,
                            "size": "sm",
                            "flex": 2,
                            "align": "end",
                            "color": plan_color,
                            "weight": "bold",
                        },
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
            "styles": {
                "body": {"backgroundColor": "#FFFFFF"}  # 白背景で読みやすく
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": contents,
            },
        },
    }

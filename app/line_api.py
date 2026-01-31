import uuid
import os
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import httpx

LINE_PROFILE_API = "https://api.line.me/v2/bot/profile/{}"
LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"

# Rich menu APIs
LINE_LINK_RICH_MENU_API = "https://api.line.me/v2/bot/user/{}/richmenu/{}"
LINE_UNLINK_RICH_MENU_API = "https://api.line.me/v2/bot/user/{}/richmenu"

# ✅ 表示順を保証（1Mは完全削除）
PLAN_ORDER = ["3m", "6m", "12m"]

# ✅ 表示ラベル（必要なら金額だけ変えればOK）
PLAN_LABELS = {
    "3m": "3か月（¥12,000）",
    "6m": "6か月（¥18,000）",
    "12m": "12か月（¥36,000）",
}


def _token() -> str:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()


def _rich_menu_preagree_id() -> str:
    """未同意ユーザー向けのリッチメニューID（未設定でもOK）"""
    return os.getenv("LINE_RICH_MENU_PREAGREE_ID", "").strip()


def _rich_menu_main_id() -> str:
    """同意済ユーザー向けのリッチメニューID（未設定でもOK）"""
    return os.getenv("LINE_RICH_MENU_MAIN_ID", "").strip()


def _stripe_payment_link(plan_tag: str) -> str:
    """Stripe Payment Link のURLを返す。
    優先順位: tasks.stripe_payment_link -> STRIPE_PAYMENT_LINK_<PLAN> -> STRIPE_PAYMENT_LINK_URL
    """
    tag = (plan_tag or "").strip().upper() or "FREE"
    url = os.getenv(f"STRIPE_PAYMENT_LINK_{tag}", "").strip()
    if url:
        return url
    return os.getenv("STRIPE_PAYMENT_LINK_URL", "").strip()


def _stripe_payment_links_from_env() -> Dict[str, str]:
    """Stripe Payment Link（期間別）を環境変数から取得。
    期待する環境変数:
      - STRIPE_PAYMENT_LINK_3M
      - STRIPE_PAYMENT_LINK_6M
      - STRIPE_PAYMENT_LINK_12M
    """
    return {
        "3m": os.getenv("STRIPE_PAYMENT_LINK_3M", "").strip(),
        "6m": os.getenv("STRIPE_PAYMENT_LINK_6M", "").strip(),
        "12m": os.getenv("STRIPE_PAYMENT_LINK_12M", "").strip(),
    }


def _stripe_payment_links(task: Dict[str, Any], plan_tag: str) -> Dict[str, str]:
    """Stripeの決済リンク（期間別）を返す。
    優先順位:
      1) DB (task.stripe_payment_link_3m / _6m / _12m) があればそれ
      2) env (STRIPE_PAYMENT_LINK_3M / 6M / 12M)
      3) legacy: 単一リンク (task.stripe_payment_link → STRIPE_PAYMENT_LINK_<PLAN> → STRIPE_PAYMENT_LINK_URL)
         ※ 1mは禁止 → 3mへフォールバック
    """
    d = {
        "3m": (task.get("stripe_payment_link_3m") or "").strip(),
        "6m": (task.get("stripe_payment_link_6m") or "").strip(),
        "12m": (task.get("stripe_payment_link_12m") or "").strip(),
    }

    env_d = _stripe_payment_links_from_env()
    for k in d:
        if not d[k] and env_d.get(k):
            d[k] = env_d[k]

    # legacy fallback（1mは禁止。単一リンクしか無い場合は 3m に寄せる）
    if not any(d.values()):
        legacy = (task.get("stripe_payment_link") or "").strip() or _stripe_payment_link(plan_tag)
        if legacy:
            d["3m"] = legacy

    return d


def _with_client_reference_id(base_url: str, client_reference_id: str) -> str:
    """Payment Link に client_reference_id を付与して返す（既存クエリも保持）"""
    if not base_url:
        return ""
    try:
        u = urlparse(base_url)
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        q["client_reference_id"] = client_reference_id
        new_q = urlencode(q, doseq=True)
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))
    except Exception:
        # 最悪でも末尾に付ける（安全側）
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}client_reference_id={client_reference_id}"


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


async def unlink_rich_menu_from_user(user_id: str) -> bool:
    """ユーザーのリッチメニュー紐付け解除（未設定でも 200/404/204 を想定）"""
    token = _token()
    if not token:
        return False
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.delete(LINE_UNLINK_RICH_MENU_API.format(user_id), headers=headers, timeout=10)
        return r.status_code in (200, 204, 404)
    except Exception:
        return False


async def link_rich_menu_to_user(user_id: str, rich_menu_id: str) -> bool:
    """ユーザーにリッチメニューを紐付け"""
    token = _token()
    if not token:
        return False
    rich_menu_id = (rich_menu_id or "").strip()
    if not rich_menu_id:
        return False
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(LINE_LINK_RICH_MENU_API.format(user_id, rich_menu_id), headers=headers, timeout=10)
        if r.status_code not in (200, 201):
            print("LINE link rich menu failed:", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        print("LINE link rich menu exception:", repr(e))
        return False


async def set_user_rich_menu(user_id: str, *, agreed: bool) -> bool:
    """同意状態に応じてリッチメニューを切り替え（ID未設定なら何もしない）"""
    rich_menu_id = _rich_menu_main_id() if agreed else _rich_menu_preagree_id()
    if not rich_menu_id:
        return False
    await unlink_rich_menu_from_user(user_id)
    return await link_rich_menu_to_user(user_id, rich_menu_id)


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


def _format_yyyy_mm_dd(value) -> str:
    """決済日などを YYYY/MM/DD で表示"""
    if not value:
        return "-"
    try:
        if isinstance(value, datetime):
            return value.strftime("%Y/%m/%d")
        s = str(value)
        # date / 'YYYY-MM-DD'
        try:
            dt = datetime.fromisoformat(s)
            return dt.strftime("%Y/%m/%d")
        except Exception:
            return s[:10].replace("-", "/")
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
                {"type": "text", "text": "タスク名", "size": "xs", "weight": "bold", "flex": 6, "align": "center", "color": "#111111", "wrap": False, "maxLines": 1},
                {"type": "text", "text": "実行時間", "size": "xs", "weight": "bold", "flex": 3, "align": "center", "color": "#111111", "wrap": False, "maxLines": 1},
                {"type": "text", "text": "期限",     "size": "xs", "weight": "bold", "flex": 3, "align": "center", "color": "#111111", "wrap": False, "maxLines": 1},
                {"type": "text", "text": "ステート",   "size": "xs", "weight": "bold", "flex": 3, "align": "center", "color": "#111111", "wrap": False, "maxLines": 1},
            ],
        },
        {"type": "separator", "margin": "sm"},
    ]

    if not tasks:
        contents.append({"type": "text", "text": "タスクがありません。", "size": "sm", "color": "#666666", "margin": "md", "wrap": True})
    else:
        for t in tasks[:20]:
            task_id = str(t.get("task_id") or "").strip()
            name = t.get("name") or "-"
            time = t.get("schedule_value") or "-"
            plan = (t.get("plan_tag") or "free").lower()
            enabled = bool(t.get("enabled", True))

            expires_text = _format_yy_mm_dd(t.get("expires_at"))

            is_gray = not enabled
            row_color = "#AAAAAA" if is_gray else "#222222"
            plan_color = (
                "#AAAAAA" if is_gray
                else "#B42318" if plan == "paid"
                else "#666666" if plan == "expired"
                else "#1A7F37"
            )
            status_suffix = "（disabled）" if is_gray else ""

            # タップで詳細表示（Postback）
            name_action: Dict[str, Any] = {}
            if task_id:
                name_action = {
                    "type": "postback",
                    "data": f"action=task_detail&task_id={task_id}",
                    "displayText": f"{name} 詳細",
                }

            contents.append(
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "margin": "sm",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"{name}{status_suffix}",
                            "size": "xs",
                            "wrap": False,
                            "maxLines": 1,  # ← はみ出たら …
                            "flex": 6,
                            "color": row_color,
                            **({"action": name_action} if name_action else {}),
                        },
                        {"type": "text", "text": time,         "size": "xs", "flex": 3, "align": "center", "color": row_color, "wrap": False, "maxLines": 1},
                        {"type": "text", "text": expires_text, "size": "xs", "flex": 3, "align": "center", "color": row_color, "wrap": False, "maxLines": 1},
                        {"type": "text", "text": plan,         "size": "xs", "flex": 2, "align": "center", "color": plan_color, "wrap": False, "maxLines": 1},
                    ],
                }
            )

        # ✅ タスクがあるときだけ注記を表示（テーブルの下）
        contents.append(
            {
                "type": "text",
                "text": "※ タスク名をタップで詳細表示できます。",
                "size": "xxs",
                "color": "#999999",
                "wrap": True,
                "margin": "md",
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


def build_task_detail_flex(user_name: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """タスク詳細をFlexで返す（必要ならStripe決済リンクボタン付き）"""
    name = task.get("name") or "-"
    schedule_value = task.get("schedule_value") or "-"
    plan_tag = (task.get("plan_tag") or "free").lower()
    expires_at = _format_yyyy_mm_dd(task.get("expires_at"))
    payment_date = _format_yyyy_mm_dd(task.get("payment_date"))
    payment_amount = (task.get("payment_amount") or "-").strip() or "-"
    notes = (task.get("notes") or "-").strip() or "-"

    # Stripe決済リンク（期間別）を取得（なければ legacy 単一リンクにフォールバック）
    pay_links = _stripe_payment_links(task, plan_tag)

    rows = [
        ("タスク名：", name),
        ("実行時間：", schedule_value),
        ("ステート：", plan_tag),
        ("有効期限：", expires_at),
        ("お支払い日：", payment_date),
        ("お支払い金額：", payment_amount),
        ("ノート：", notes),
    ]

    contents: List[Dict[str, Any]] = []
    for i, (label, value) in enumerate(rows):
        if i:
            contents.append({"type": "separator", "margin": "md"})
        contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "contents": [
                    {"type": "text", "text": label, "size": "xs", "color": "#666666"},
                    {"type": "text", "text": value, "size": "sm", "color": "#222222", "wrap": True},
                ],
            }
        )

    bubble: Dict[str, Any] = {
        "type": "bubble",
        "styles": {"body": {"backgroundColor": "#FFFFFF"}},
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": "タスク詳細", "weight": "bold", "size": "md"},
                {"type": "separator", "margin": "md"},
                *contents,
            ],
        },
    }

    # ✅ 決済リンクがあるときだけボタンを付ける（期間別: 3/6/12か月）
    buttons: List[Dict[str, Any]] = []
    task_id = str(task.get("task_id") or "").strip()

    for plan in PLAN_ORDER:
        url = (pay_links.get(plan) or "").strip()
        if not url:
            continue

        label = PLAN_LABELS.get(plan, plan)
        uri = _with_client_reference_id(url, f"{task_id}_{plan}") if task_id else url

        buttons.append(
            {
                "type": "button",
                "style": "primary" if plan != "12m" else "primary",
                "height": "sm",
                "action": {"type": "uri", "label": label, "uri": uri},
            }
        )

    if buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": "決済リンク", "size": "sm", "weight": "bold", "color": "#111111"},
                {"type": "separator", "margin": "sm"},
                *buttons,
            ],
        }

    return {
        "type": "flex",
        "altText": f"タスク詳細：{name}",
        "contents": bubble,
    }


def build_terms_agreement_flex(current_ver: str, terms_url: str, privacy_url: str = "") -> Dict[str, Any]:
    """利用規約への同意を促す Flex メッセージ（Postback で同意）"""
    buttons = [
        {
            "type": "button",
            "style": "link",
            "height": "sm",
            "action": {"type": "uri", "label": f"利用規約を開く（Ver.{current_ver}）", "uri": terms_url},
        }
    ]
    if privacy_url:
        buttons.append(
            {
                "type": "button",
                "style": "link",
                "height": "sm",
                "action": {"type": "uri", "label": "プライバシーポリシーを開く", "uri": privacy_url},
            }
        )

    buttons.append(
        {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "action": {
                "type": "postback",
                "label": "同意して開始",
                "data": f"action=agree_terms&ver={current_ver}",
            },
        }
    )

    return {
        "type": "flex",
        "altText": "利用規約への同意が必要です",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "利用開始前の確認", "weight": "bold", "size": "lg", "wrap": True},
                    {
                        "type": "text",
                        "text": "サービスのご利用には、利用規約・プライバシーポリシーへの同意が必要です。",
                        "size": "sm",
                        "wrap": True,
                        "color": "#333333",
                    },
                    {"type": "separator", "margin": "md"},
                    {"type": "box", "layout": "vertical", "spacing": "sm", "margin": "md", "contents": buttons},
                ],
            },
        },
    }

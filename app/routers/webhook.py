import uuid
import base64
import hashlib
import hmac
import os
from typing import Any, Dict, Optional

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.line_api import (
    build_tasks_flex,
    build_task_detail_flex,
    build_terms_agreement_flex,
    fetch_line_profile,
    reply_message,
    set_user_rich_menu,
)

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
def _current_terms_version() -> str:
    v = os.getenv("CURRENT_TERMS_VERSION", "1.0").strip()
    return v or "1.0"


def _terms_url(current_ver: str) -> str:
    url = os.getenv("TERMS_URL", "").strip()
    if url:
        return url
    # 同一サーバーで配信する想定（相対URL）
    return f"/terms?v={current_ver}"


def _privacy_url() -> str:
    return os.getenv("PRIVACY_URL", "").strip()


def _parse_postback_data(data: str) -> Dict[str, str]:
    """action=agree_terms&ver=1.3 のような data を dict にする"""
    out: Dict[str, str] = {}
    for part in (data or "").split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
        else:
            out[part] = ""
    return out


async def _has_agreed_current_terms(pool: asyncpg.Pool, user_id: str, current_ver: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT agreed_terms_version FROM users WHERE user_id=$1", user_id)
    if not row:
        return False
    return (row["agreed_terms_version"] or "").strip() == current_ver


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


def _extract_line_destination(ev: Dict[str, Any]) -> Optional[str]:
    """LINEの宛先ID（U/C/R...）をイベントから取り出す"""
    src = ev.get("source") or {}
    t = src.get("type")
    if t == "group":
        return src.get("groupId")
    if t == "room":
        return src.get("roomId")
    if t == "user":
        return src.get("userId")
    return None


async def upsert_line_conversation(conn: asyncpg.Connection, ev: Dict[str, Any]) -> None:
    """イベントを受け取ったタイミングで conversations を自動保存（UPSERT）"""
    dest = _extract_line_destination(ev)
    if not dest:
        return
    sql = """
    INSERT INTO conversations (provider, destination, last_seen_at)
    VALUES ('line', $1, NOW())
    ON CONFLICT (provider, destination)
    DO UPDATE SET last_seen_at=NOW()
    """
    await conn.execute(sql, dest)


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
    SELECT task_id, name, schedule_value, plan_tag, expires_at, enabled
    FROM tasks
    WHERE user_id=$1
    ORDER BY created_at DESC
    LIMIT 50
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, user_id)
    return [dict(r) for r in rows]


async def fetch_task_detail_for_user(pool: asyncpg.Pool, user_id: str, task_id: str) -> Optional[dict]:
    """task_id 指定で詳細を取得（user_id も一致するもののみ）"""
    sql = """
    SELECT task_id, name, schedule_value, plan_tag, payment_date, payment_amount, notes
    FROM tasks
    WHERE user_id=$1 AND task_id=$2::uuid
    LIMIT 1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, user_id, task_id)
    return dict(row) if row else None


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
        ev_type = ev.get("type")
        reply_token = ev.get("replyToken")
        user_id = (ev.get("source") or {}).get("userId")
        if not reply_token or not user_id:
            continue

        # プロフィール保存（displayName 等）
        profile = await fetch_line_profile(user_id)
        display_name = profile.get("displayName") or "user"

        async with pool.acquire() as conn:
            await upsert_line_conversation(conn, ev)  # ✅ groupId/roomId などを自動保存
            await upsert_user_from_profile(conn, user_id, profile)

        current_ver = _current_terms_version()

        # ==========================
        # Follow（友だち追加）
        # ==========================
        if ev_type == "follow":
            # 未同意用リッチメニュー（任意：IDが未設定なら何もしない）
            await set_user_rich_menu(user_id, agreed=False)
            flex = build_terms_agreement_flex(current_ver, _terms_url(current_ver), _privacy_url())
            await reply_message(reply_token, [flex])
            continue

        # ==========================
        # Postback（同意など）
        # ==========================
        if ev_type == "postback":
            data = (ev.get("postback") or {}).get("data") or ""
            pb = _parse_postback_data(data)

            # ==========================
            # タスク詳細（タスク名タップ）
            # ==========================
            if pb.get("action") == "task_detail":
                task_id = (pb.get("task_id") or "").strip()
                if not task_id:
                    await reply_message(reply_token, [{"type": "text", "text": "タスクIDが取得できませんでした。"}])
                    continue

                task = await fetch_task_detail_for_user(pool, user_id, task_id)
                if not task:
                    await reply_message(reply_token, [{"type": "text", "text": "タスクが見つかりませんでした。"}])
                    continue

                flex = build_task_detail_flex(display_name, task)
                await reply_message(reply_token, [flex])
                continue

            if pb.get("action") == "agree_terms":
                agreed_ver = (pb.get("ver") or current_ver).strip() or current_ver

                # ✅ すでに同じバージョンに同意済みなら、再送しない（=返信しない）
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT agreed_terms_version FROM users WHERE user_id=$1",
                        user_id,
                    )
                if row and (row["agreed_terms_version"] or "").strip() == agreed_ver:
                    # 任意：同意済みメニューへ寄せる（ID未設定なら何もしない）
                    await set_user_rich_menu(user_id, agreed=True)
                    continue

                # 初回同意（または新バージョン同意）のときだけ保存＆返信
                async with pool.acquire() as conn:
                    # 同意ログ（同じ版は1回だけ）
                    await conn.execute(
                        """
                        INSERT INTO terms_agreements (user_id, terms_version, channel, source)
                        VALUES ($1, $2, 'line', 'postback')
                        ON CONFLICT (user_id, terms_version) DO NOTHING
                        """,
                        user_id,
                        agreed_ver,
                    )
                    # ユーザー側に「最新同意」をキャッシュ
                    await conn.execute(
                        """
                        UPDATE users
                        SET agreed_terms_version=$2, agreed_terms_at=NOW()
                        WHERE user_id=$1
                        """,
                        user_id,
                        agreed_ver,
                    )

                await reply_message(
                    reply_token,
                    [
                        {
                            "type": "text",
                            "text": (
                                "利用規約へのご同意、ありがとうございます。\n"
                                "ご質問やご相談がありましたら、お気軽にお声がけください。"
                            ),
                        }
                    ],
                )

                # 同意済みリッチメニューへ（任意：IDが未設定なら何もしない）
                await set_user_rich_menu(user_id, agreed=True)

            continue

        # ==========================
        # Text message
        # ==========================
        if ev_type != "message":
            continue

        message = ev.get("message") or {}
        if message.get("type") != "text":
            continue

        text = message.get("text") or ""

        # ✅ 規約同意ゲート（未同意ならここで止める）
        if not await _has_agreed_current_terms(pool, user_id, current_ver):
            # 未同意（または規約更新で再同意が必要）なら未同意メニューに戻す
            await set_user_rich_menu(user_id, agreed=False)
            flex = build_terms_agreement_flex(current_ver, _terms_url(current_ver), _privacy_url())
            await reply_message(reply_token, [flex])
            continue

        # ==========================
        # 既存コマンド
        # ==========================
        if is_tasks_command(text):
            tasks = await fetch_tasks_for_user(pool, user_id)
            flex = build_tasks_flex(display_name, tasks)
            await reply_message(reply_token, [flex])
            continue

        task_name = parse_rerun_command(text)
        if task_name:
            result = await enqueue_rerun(pool, user_id, task_name, requested_by=display_name)

            if result["ok"]:
                msg = f"「{task_name}」を再実行キューに追加しました。再実行までしばらくお待ちください。"
            else:
                reason = result.get("reason")
                if reason == "not_found":
                    msg = f"「{task_name}」が見つかりませんでした。"
                elif reason == "disabled":
                    msg = f"「{task_name}」は disabled です。"
                elif reason == "already_pending":
                    msg = f"「{task_name}」はすでに再実行待ち/実行中です。"
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

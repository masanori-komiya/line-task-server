from __future__ import annotations

import hmac
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request, HTTPException


router = APIRouter()


JST = ZoneInfo("Asia/Tokyo")


@dataclass
class StripeSig:
    timestamp: int
    v1: str


def _parse_stripe_signature(header: str) -> StripeSig:
    # Stripe-Signature: t=1492774577,v1=5257a869e7...,v0=...
    parts = {}
    for item in header.split(","):
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        parts[k.strip()] = v.strip()
    if "t" not in parts or "v1" not in parts:
        raise ValueError("invalid stripe signature header")
    return StripeSig(timestamp=int(parts["t"]), v1=parts["v1"])


def _verify_stripe_signature(raw_body: bytes, header: str, secret: str, tolerance_sec: int = 300) -> None:
    sig = _parse_stripe_signature(header)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if abs(now_ts - sig.timestamp) > tolerance_sec:
        raise ValueError("timestamp outside tolerance")

    signed_payload = f"{sig.timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig.v1):
        raise ValueError("signature mismatch")


def _extract_task_id_and_plan(client_reference_id: str) -> Tuple[str, Optional[str]]:
    # Expected: <uuid>_<plan>  (plan: 1m/3m/6m)
    s = (client_reference_id or "").strip()
    if not s:
        return "", None
    if "_" in s:
        task_id, plan = s.rsplit("_", 1)
        return task_id, plan
    return s, None


def _add_months(dt: datetime, months: int) -> datetime:
    # Simple month add that preserves day where possible.
    import calendar

    year = dt.year
    month = dt.month + months
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        # misconfiguration
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET is not set")

    raw = await request.body()
    header = request.headers.get("stripe-signature")
    if not header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        _verify_stripe_signature(raw, header, secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_id = str(event.get("id", "")).strip()
    event_type = str(event.get("type", "")).strip()

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=500, detail="db_pool is not ready")

    # Idempotency: store event_id (Stripe can retry)
    if event_id:
        async with pool.acquire() as conn:
            inserted = await conn.fetchval(
                """
                INSERT INTO stripe_events(event_id, payload)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
                """,
                event_id,
                json.dumps(event),
            )
        if not inserted:
            # already processed
            return {"ok": True, "duplicate": True}

    if event_type != "checkout.session.completed":
        return {"ok": True, "ignored": event_type}

    obj: Dict[str, Any] = (((event.get("data") or {}).get("object")) or {})
    client_reference_id = str(obj.get("client_reference_id", "") or "").strip()
    task_id_str, plan = _extract_task_id_and_plan(client_reference_id)

    amount_total = obj.get("amount_total")
    currency = (obj.get("currency") or "").upper()
    created_ts = obj.get("created") or event.get("created")

    if not task_id_str:
        return {"ok": True, "warning": "missing client_reference_id"}

    # Convert paid date in JST
    try:
        created_dt_utc = datetime.fromtimestamp(int(created_ts), tz=timezone.utc) if created_ts else datetime.now(timezone.utc)
    except Exception:
        created_dt_utc = datetime.now(timezone.utc)
    created_dt_jst = created_dt_utc.astimezone(JST)
    payment_date = created_dt_jst.date()

    # payment_amount: keep as string for compatibility with existing schema
    payment_amount = ""
    if isinstance(amount_total, int):
        payment_amount = str(amount_total)
        if currency:
            payment_amount = f"{amount_total} {currency}"  # e.g., 12000 JPY
    elif amount_total is not None:
        payment_amount = str(amount_total)

    # Update tasks table
    async with pool.acquire() as conn:
        # Load current expires_at to extend (optional)
        row = await conn.fetchrow(
            "SELECT task_id, expires_at FROM tasks WHERE task_id = $1::uuid",
            task_id_str,
        )
        if not row:
            return {"ok": True, "warning": "task not found", "task_id": task_id_str}

        expires_at = row["expires_at"]
        new_expires_at = expires_at
        if plan in {"1m", "3m", "6m"}:
            months = int(plan[0])  # 1/3/6
            base = created_dt_jst
            if expires_at is not None:
                try:
                    base = max(base, expires_at.astimezone(JST))
                except Exception:
                    base = created_dt_jst
            new_expires_at = _add_months(base, months)

        await conn.execute(
            """
            UPDATE tasks
            SET payment_date = $2,
                payment_amount = $3,
                expires_at = COALESCE($4, expires_at),
                updated_at = NOW()
            WHERE task_id = $1::uuid
            """,
            task_id_str,
            payment_date,
            payment_amount,
            new_expires_at,
        )

    return {
        "ok": True,
        "task_id": task_id_str,
        "plan": plan,
        "payment_date": str(payment_date),
        "payment_amount": payment_amount,
    }

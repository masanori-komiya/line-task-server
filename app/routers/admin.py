import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")

TIME_RE = re.compile(r"^\d{2}:\d{2}$")
RUN_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
PLAN_TAGS = {"free", "paid"}
JST = ZoneInfo("Asia/Tokyo")

# rerun queue statuses
RERUN_STATUSES = {"queued", "running", "done", "failed", "canceled"}


@router.get("/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        users = await conn.fetch(
            """
            SELECT user_id, user_name, picture_url, status_message, last_event, last_seen_at
            FROM users
            ORDER BY last_seen_at DESC
            LIMIT 400
            """
        )
    return templates.TemplateResponse("admin_users.html", {"request": request, "title": "Users", "users": users})


@router.get("/users/{user_id}/tasks", response_class=HTMLResponse)
async def admin_user_tasks(request: Request, user_id: str):
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT user_id, user_name, picture_url, status_message, last_event, last_seen_at FROM users WHERE user_id=$1",
            user_id,
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        tasks = await conn.fetch(
            """
            SELECT task_id, user_id, name, script_key, schedule_type, schedule_value, timezone,
                   enabled, notes, plan_tag, expires_at,
                   pc_name,
                   to_char(run_time, 'HH24:MI:SS') AS run_time_hms,
                   is_pc_specific,
                   created_at, updated_at
            FROM tasks
            WHERE user_id=$1
            ORDER BY created_at DESC
            """,
            user_id,
        )

    return templates.TemplateResponse(
        "admin_tasks.html",
        {"request": request, "title": "Tasks", "user": user, "tasks": tasks},
    )


@router.post("/users/{user_id}/tasks")
async def admin_create_task(
    request: Request,
    user_id: str,
    name: str = Form(...),
    script_key: str = Form(...),
    schedule_value: str = Form(...),
    plan_tag: str = Form("free"),
    expires_date: Optional[str] = Form(None),  # YYYY-MM-DD
    pc_name: str = Form("default"),
    run_time: str = Form("00:00:00"),
    is_pc_specific: str = Form("false"),
    notes: Optional[str] = Form(None),
):
    if not TIME_RE.match(schedule_value.strip()):
        raise HTTPException(status_code=400, detail="schedule_value must be HH:MM")

    pc_name = (pc_name or "default").strip() or "default"

    run_time = (run_time or "00:00:00").strip() or "00:00:00"
    if not RUN_TIME_RE.match(run_time):
        raise HTTPException(status_code=400, detail="run_time must be HH:MM:SS")

    is_pc_specific_bool = (is_pc_specific or "false").strip().lower() in {"true", "1", "yes", "on"}

    plan_tag = (plan_tag or "free").strip()
    if plan_tag not in PLAN_TAGS:
        raise HTTPException(status_code=400, detail="plan_tag must be free or paid")

    expires_at = None
    if expires_date:
        try:
            d = datetime.fromisoformat(expires_date.strip())
            expires_at = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=JST)
        except Exception:
            raise HTTPException(status_code=400, detail="expires_date must be YYYY-MM-DD")

    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1", user_id)
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")

        await conn.execute(
            """
            INSERT INTO tasks (user_id, name, script_key, schedule_type, schedule_value, timezone,
                               enabled, notes, plan_tag, expires_at,
                               pc_name, run_time, is_pc_specific)
            VALUES ($1, $2, $3, 'daily_time', $4, 'Asia/Tokyo',
                    TRUE, $5, $6, $7,
                    $8, $9::interval, $10)
            """,
            user_id,
            name.strip(),
            script_key.strip(),
            schedule_value.strip(),
            (notes or "").strip() or None,
            plan_tag,
            expires_at,
            pc_name,
            run_time,
            is_pc_specific_bool,
        )

    return RedirectResponse(url=f"/admin/users/{user_id}/tasks", status_code=303)


@router.post("/tasks/{task_id}/update")
async def admin_update_task_meta(
    request: Request,
    task_id: str,
    pc_name: str = Form("default"),
    run_time: str = Form("00:00:00"),
    is_pc_specific: str = Form("false"),
):
    pc_name = (pc_name or "default").strip() or "default"

    run_time = (run_time or "00:00:00").strip() or "00:00:00"
    if not RUN_TIME_RE.match(run_time):
        raise HTTPException(status_code=400, detail="run_time must be HH:MM:SS")

    is_pc_specific_bool = (is_pc_specific or "false").strip().lower() in {"true", "1", "yes", "on"}

    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM tasks WHERE task_id=$1", task_id)
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        user_id = row["user_id"]

        await conn.execute(
            """
            UPDATE tasks
            SET pc_name=$1,
                run_time=$2::interval,
                is_pc_specific=$3,
                updated_at=NOW()
            WHERE task_id=$4
            """,
            pc_name,
            run_time,
            is_pc_specific_bool,
            task_id,
        )

    return RedirectResponse(url=f"/admin/users/{user_id}/tasks", status_code=303)


@router.post("/tasks/{task_id}/toggle")
async def admin_toggle_task(request: Request, task_id: str):
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id, enabled FROM tasks WHERE task_id=$1", task_id)
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        new_enabled = not bool(row["enabled"])
        await conn.execute("UPDATE tasks SET enabled=$1, updated_at=NOW() WHERE task_id=$2", new_enabled, task_id)
        user_id = row["user_id"]
    return RedirectResponse(url=f"/admin/users/{user_id}/tasks", status_code=303)


@router.post("/tasks/{task_id}/delete")
async def admin_delete_task(request: Request, task_id: str):
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM tasks WHERE task_id=$1", task_id)
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        user_id = row["user_id"]
        await conn.execute("DELETE FROM tasks WHERE task_id=$1", task_id)
    return RedirectResponse(url=f"/admin/users/{user_id}/tasks", status_code=303)


# ======================================================
# Rerun queue (再実行リスト)
# ======================================================

@router.get("/rerun-queue", response_class=HTMLResponse)
async def admin_rerun_queue(request: Request, status: str = "active"):
    """
    status:
      - active: queued + running
      - queued / running / done / failed / canceled
      - all
    """
    status = (status or "active").strip().lower()
    pool = request.app.state.db_pool

    where_sql = ""
    args = []
    if status == "active":
        where_sql = "WHERE q.status IN ('queued','running')"
    elif status == "all":
        where_sql = ""
    elif status in RERUN_STATUSES:
        where_sql = "WHERE q.status=$1"
        args = [status]
    else:
        raise HTTPException(status_code=400, detail="invalid status")

    sql = f"""
    SELECT
      q.request_id,
      q.status,
      (q.requested_at AT TIME ZONE 'Asia/Tokyo') AS requested_at_jst,
      (q.locked_at    AT TIME ZONE 'Asia/Tokyo') AS locked_at_jst,
      q.locked_by,
      (q.started_at   AT TIME ZONE 'Asia/Tokyo') AS started_at_jst,
      (q.finished_at  AT TIME ZONE 'Asia/Tokyo') AS finished_at_jst,
      q.exit_code,
      q.pc_name AS original_pc_name,
      q.requested_by,

      t.task_id,
      t.name AS task_name,
      t.script_key,
      t.pc_name AS task_pc_name,

      u.user_id,
      u.user_name
    FROM task_rerun_queue q
    JOIN tasks t ON t.task_id = q.task_id
    JOIN users u ON u.user_id = q.user_id
    {where_sql}
    ORDER BY
      CASE q.status
        WHEN 'running' THEN 0
        WHEN 'queued'  THEN 1
        ELSE 2
      END,
      q.requested_at DESC
    LIMIT 400
    """

    async with pool.acquire() as conn:
        items = await conn.fetch(sql, *args)
        counts = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE status='queued')   AS queued,
              COUNT(*) FILTER (WHERE status='running')  AS running,
              COUNT(*) FILTER (WHERE status='done')     AS done,
              COUNT(*) FILTER (WHERE status='failed')   AS failed,
              COUNT(*) FILTER (WHERE status='canceled') AS canceled,
              COUNT(*) AS all
            FROM task_rerun_queue
            """
        )

    return templates.TemplateResponse(
        "admin_rerun_queue.html",
        {"request": request, "title": "Rerun Queue", "items": items, "status": status, "counts": counts},
    )


@router.post("/rerun-queue/{request_id}/cancel")
async def admin_cancel_rerun(request: Request, request_id: str):
    """Cancel queued item (runningは不可)."""
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM task_rerun_queue WHERE request_id=$1", request_id)
        if not row:
            raise HTTPException(status_code=404, detail="request not found")
        if row["status"] != "queued":
            raise HTTPException(status_code=400, detail="only queued can be canceled")
        await conn.execute(
            "UPDATE task_rerun_queue SET status='canceled', finished_at=NOW() WHERE request_id=$1",
            request_id,
        )
    return RedirectResponse(url="/admin/rerun-queue?status=active", status_code=303)


@router.post("/rerun-queue/{request_id}/delete")
async def admin_delete_rerun(request: Request, request_id: str):
    """Delete a rerun record (done/failed/canceled only)."""
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM task_rerun_queue WHERE request_id=$1", request_id)
        if not row:
            raise HTTPException(status_code=404, detail="request not found")
        if row["status"] in ("queued", "running"):
            raise HTTPException(status_code=400, detail="active item cannot be deleted")
        await conn.execute("DELETE FROM task_rerun_queue WHERE request_id=$1", request_id)
    return RedirectResponse(url="/admin/rerun-queue?status=all", status_code=303)

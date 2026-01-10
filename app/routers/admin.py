import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="app/templates")

TIME_RE = re.compile(r"^\d{2}:\d{2}$")


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
            SELECT task_id, user_id, name, script_key, schedule_type, schedule_value, timezone, enabled, notes, created_at, updated_at
            FROM tasks
            WHERE user_id=$1
            ORDER BY created_at DESC
            """,
            user_id,
        )

    script_keys = [
        ("bus_am", "バス予約（朝）"),
        ("bus_pm", "バス予約（夕）"),
        ("trash_notify", "ゴミ出し通知"),
    ]

    return templates.TemplateResponse(
        "admin_tasks.html",
        {"request": request, "title": "Tasks", "user": user, "tasks": tasks, "script_keys": script_keys},
    )


@router.post("/users/{user_id}/tasks")
async def admin_create_task(
    request: Request,
    user_id: str,
    name: str = Form(...),
    script_key: str = Form(...),
    schedule_value: str = Form(...),
    notes: Optional[str] = Form(None),
):
    if not TIME_RE.match(schedule_value):
        raise HTTPException(status_code=400, detail="schedule_value must be HH:MM")

    pool = request.app.state.db_pool

    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id=$1", user_id)
        if not exists:
            raise HTTPException(status_code=404, detail="User not found")

        await conn.execute(
            """
            INSERT INTO tasks (user_id, name, script_key, schedule_type, schedule_value, timezone, enabled, notes)
            VALUES ($1, $2, $3, 'daily_time', $4, 'Asia/Tokyo', TRUE, $5)
            """,
            user_id,
            name.strip(),
            script_key.strip(),
            schedule_value.strip(),
            (notes or "").strip() or None,
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
        await conn.execute(
            "UPDATE tasks SET enabled=$1, updated_at=NOW() WHERE task_id=$2",
            new_enabled,
            task_id,
        )
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

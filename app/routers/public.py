from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request, "title": "Home"})


@router.get("/health")
async def health(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if not pool:
        return JSONResponse({"status": "ng", "db_pool": "missing"})

    async with pool.acquire() as conn:
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        tasks_count = await conn.fetchval("SELECT COUNT(*) FROM tasks")
        return JSONResponse({
            "status": "ok",
            "db": "postgres",
            "users_count": int(users_count),
            "tasks_count": int(tasks_count),
        })


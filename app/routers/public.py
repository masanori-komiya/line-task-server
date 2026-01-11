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
    try:
        async with pool.acquire() as conn:
            n = await conn.fetchval("SELECT 1")
        return JSONResponse({"status": "ok", "db": "postgres", "select_1": int(n)})
    except Exception as e:
        return JSONResponse({"status": "ng", "db": "postgres_error", "error": str(e)})

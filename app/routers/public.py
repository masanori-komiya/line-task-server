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
    return JSONResponse({"status": "ok", "db": "postgres" if pool else "missing"})

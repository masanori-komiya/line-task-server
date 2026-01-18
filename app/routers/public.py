import uuid
import os

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


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request, v: str | None = None):
    current_ver = (os.getenv("CURRENT_TERMS_VERSION", "1.0").strip() or "1.0")
    ver = (v or current_ver).strip() or current_ver
    body = os.getenv("TERMS_BODY", "").strip()

    html_body = body.replace("\n", "<br>") if body else (
        "（TERMS_BODY が未設定です。<br>"
        "環境変数 TERMS_BODY に利用規約本文を入れるか、TERMS_URL を外部URLに設定してください。）"
    )

    html = f"""
    <html><head><meta charset="utf-8"><title>利用規約 Ver.{ver}</title></head>
    <body style="font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,'Noto Sans JP','Hiragino Kaku Gothic ProN',Meiryo,sans-serif; line-height:1.6; padding:24px;">
      <h1>利用規約（Ver.{ver}）</h1>
      <p style="color:#666;">最終改定日：{os.getenv("TERMS_UPDATED_AT","").strip() or "（未設定）"}</p>
      <hr>
      <div>{html_body}</div>
    </body></html>
    """
    return HTMLResponse(html)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    body = os.getenv("PRIVACY_BODY", "").strip()
    html_body = body.replace("\n", "<br>") if body else (
        "（PRIVACY_BODY が未設定です。<br>"
        "環境変数 PRIVACY_BODY にプライバシーポリシー本文を入れるか、PRIVACY_URL を外部URLに設定してください。）"
    )

    html = f"""
    <html><head><meta charset="utf-8"><title>プライバシーポリシー</title></head>
    <body style="font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,'Noto Sans JP','Hiragino Kaku Gothic ProN',Meiryo,sans-serif; line-height:1.6; padding:24px;">
      <h1>プライバシーポリシー</h1>
      <p style="color:#666;">最終改定日：{os.getenv("PRIVACY_UPDATED_AT","").strip() or "（未設定）"}</p>
      <hr>
      <div>{html_body}</div>
    </body></html>
    """
    return HTMLResponse(html)

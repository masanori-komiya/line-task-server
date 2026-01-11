from fastapi import FastAPI

from app.db import create_pool, init_db
from app.routers.public import router as public_router
from app.routers.admin import router as admin_router
from app.routers.webhook import router as webhook_router

app = FastAPI()

# ルーター
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(webhook_router)

@app.on_event("startup")
async def on_startup() -> None:
    pool = await create_pool()
    # ★ ここは webhook.py 側も request.app.state.db_pool で参照する前提
    app.state.db_pool = pool
    await init_db(pool)

@app.on_event("shutdown")
async def on_shutdown() -> None:
    pool = getattr(app.state, "db_pool", None)
    if pool:
        await pool.close()

@app.get("/debug/routes")
def debug_routes():
    out = []
    for r in app.routes:
        methods = sorted(getattr(r, "methods", []) or [])
        out.append(
            {
                "path": getattr(r, "path", ""),
                "methods": methods,
                "name": getattr(r, "name", ""),
            }
        )
    return out

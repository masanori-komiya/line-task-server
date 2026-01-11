from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import create_pool, init_db
from app.routers.public import router as public_router
from app.routers.admin import router as admin_router
from app.routers.webhook import router as webhook_router

app = FastAPI()

app.include_router(public_router)
app.include_router(admin_router)
app.include_router(webhook_router)

@app.on_event("startup")
async def on_startup():
    pool = await create_pool()
    app.state.db_pool = pool
    await init_db(pool)

@app.on_event("shutdown")
async def on_shutdown():
    pool = getattr(app.state, "db_pool", None)
    if pool:
        await pool.close()

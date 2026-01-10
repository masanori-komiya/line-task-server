from fastapi import FastAPI

from app.db import create_pool, init_db
from app.routers.public import router as public_router
from app.routers.webhook import router as webhook_router
from app.routers.admin import router as admin_router

app = FastAPI()


@app.on_event("startup")
async def startup() -> None:
    pool = await create_pool()
    await init_db(pool)
    app.state.db_pool = pool


@app.on_event("shutdown")
async def shutdown() -> None:
    pool = getattr(app.state, "db_pool", None)
    if pool:
        await pool.close()


app.include_router(public_router)
app.include_router(webhook_router)
app.include_router(admin_router)

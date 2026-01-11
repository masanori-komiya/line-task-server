import os
import asyncpg

def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url

async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=get_database_url(), min_size=1, max_size=5)

async def init_db(pool: asyncpg.Pool) -> None:
    base_sql = """
    CREATE EXTENSION IF NOT EXISTS pgcrypto;

    CREATE TABLE IF NOT EXISTS users (
        user_id        TEXT PRIMARY KEY,
        user_name      TEXT,
        picture_url    TEXT,
        status_message TEXT,
        last_event     TEXT,
        last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS tasks (
        task_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id        TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        name           TEXT NOT NULL,
        script_key     TEXT NOT NULL,
        schedule_type  TEXT NOT NULL DEFAULT 'daily_time',
        schedule_value TEXT NOT NULL,
        timezone       TEXT NOT NULL DEFAULT 'Asia/Tokyo',
        enabled        BOOLEAN NOT NULL DEFAULT TRUE,
        notes          TEXT,
        plan_tag       TEXT NOT NULL DEFAULT 'free',
        expires_at     TIMESTAMPTZ NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_enabled ON tasks(enabled);
    CREATE INDEX IF NOT EXISTS idx_tasks_plan_tag ON tasks(plan_tag);

    CREATE TABLE IF NOT EXISTS task_runs (
        run_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id     UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        runner_id   TEXT,
        started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ,
        status      TEXT NOT NULL,
        exit_code   INT,
        stdout      TEXT,
        stderr      TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_task_runs_task_id ON task_runs(task_id);
    CREATE INDEX IF NOT EXISTS idx_task_runs_user_id ON task_runs(user_id);
    """
    async with pool.acquire() as conn:
        await conn.execute(base_sql)

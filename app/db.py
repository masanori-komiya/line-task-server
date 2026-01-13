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

    -- ======================================================
    -- ★ 通知先（LINE / LINE WORKS）
    -- ======================================================
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        provider        TEXT NOT NULL CHECK (provider IN ('line','lineworks')),
        destination     TEXT NOT NULL,
        display_name    TEXT,
        last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (provider, destination)
    );

    CREATE INDEX IF NOT EXISTS idx_conversations_provider ON conversations(provider);
    CREATE INDEX IF NOT EXISTS idx_conversations_last_seen ON conversations(last_seen_at);

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
        pc_name        TEXT NOT NULL DEFAULT 'default',
        run_time       INTERVAL NOT NULL DEFAULT INTERVAL '00:00:00',
        is_pc_specific BOOLEAN NOT NULL DEFAULT FALSE,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    -- ✅ タスクの通知先（任意）
    ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS conversation_id UUID NULL REFERENCES conversations(conversation_id) ON DELETE SET NULL;

    ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS pc_name TEXT NOT NULL DEFAULT 'default';

    ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS run_time INTERVAL NOT NULL DEFAULT INTERVAL '00:00:00';

    ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS is_pc_specific BOOLEAN NOT NULL DEFAULT FALSE;

    CREATE INDEX IF NOT EXISTS idx_tasks_user_id  ON tasks(user_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_enabled  ON tasks(enabled);
    CREATE INDEX IF NOT EXISTS idx_tasks_plan_tag ON tasks(plan_tag);
    CREATE INDEX IF NOT EXISTS idx_tasks_pc_name  ON tasks(pc_name);
    CREATE INDEX IF NOT EXISTS idx_tasks_conversation_id ON tasks(conversation_id);

    CREATE INDEX IF NOT EXISTS idx_tasks_is_pc_specific ON tasks(is_pc_specific);

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

    -- ======================================================
    -- ★ 再実行キュー
    -- ======================================================
    CREATE TABLE IF NOT EXISTS task_rerun_queue (
        request_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id      UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        pc_name      TEXT NOT NULL,                 -- tasks.pc_name のスナップショット（ログ用）
        requested_by TEXT,
        requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        status       TEXT NOT NULL DEFAULT 'queued', -- queued / running / done / failed / canceled
        locked_at    TIMESTAMPTZ,
        locked_by    TEXT,
        started_at   TIMESTAMPTZ,
        finished_at  TIMESTAMPTZ,
        exit_code    INT,
        stdout       TEXT,
        stderr       TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_rerun_queue_pc_status
    ON task_rerun_queue(pc_name, status, requested_at);

    CREATE INDEX IF NOT EXISTS idx_rerun_queue_task_id
    ON task_rerun_queue(task_id);

    -- ★ queued/running の間は同じ task_id を重複させない（重要）
    CREATE UNIQUE INDEX IF NOT EXISTS uq_rerun_active_task
    ON task_rerun_queue(task_id)
    WHERE status IN ('queued', 'running');
    """

    async with pool.acquire() as conn:
        await conn.execute(base_sql)

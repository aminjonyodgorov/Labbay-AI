import os
import asyncpg
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language TEXT DEFAULT 'uz',
    is_blocked BOOLEAN DEFAULT FALSE,
    joined_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transcriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    duration_seconds INT,
    file_size_bytes INT,
    language TEXT,
    raw_text TEXT,
    fixed_text TEXT,
    latency_ms INT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tx_created ON transcriptions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transcriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_error ON transcriptions(error) WHERE error IS NOT NULL;
"""


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("DATABASE_URL not set")
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    logger.info("Database pool initialized and schema ensured")
    return _pool


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def upsert_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    language: str = "uz",
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name, language, last_active_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_active_at = NOW()
            """,
            telegram_id, username, first_name, last_name, language,
        )


async def set_user_language(telegram_id: int, language: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET language = $1, last_active_at = NOW() WHERE telegram_id = $2",
            language, telegram_id,
        )


async def is_user_blocked(telegram_id: int) -> bool:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_blocked FROM users WHERE telegram_id = $1", telegram_id
        )
        return bool(row and row["is_blocked"])


async def set_blocked(telegram_id: int, blocked: bool) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_blocked = $1 WHERE telegram_id = $2",
            blocked, telegram_id,
        )


async def insert_transcription(
    user_id: int,
    duration_seconds: Optional[int],
    file_size_bytes: Optional[int],
    language: str,
    raw_text: Optional[str],
    fixed_text: Optional[str],
    latency_ms: Optional[int],
    error: Optional[str],
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO transcriptions
                (user_id, duration_seconds, file_size_bytes, language, raw_text, fixed_text, latency_ms, error)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            user_id, duration_seconds, file_size_bytes, language,
            raw_text, fixed_text, latency_ms, error,
        )


async def _fetchval(conn, sql, *args, default=0):
    try:
        v = await conn.fetchval(sql, *args)
        return v if v is not None else default
    except Exception as e:
        logger.warning(f"query failed [{sql[:60]}...]: {e}")
        return default


async def stats_overview() -> dict:
    async with pool().acquire() as conn:
        return {
            "total_users": await _fetchval(conn, "SELECT COUNT(*) FROM users"),
            "blocked_users": await _fetchval(conn, "SELECT COUNT(*) FROM users WHERE is_blocked = TRUE"),
            "active_24h": await _fetchval(conn, "SELECT COUNT(*) FROM users WHERE last_active_at > NOW() - INTERVAL '24 hours'"),
            "total_transcriptions": await _fetchval(conn, "SELECT COUNT(*) FROM transcriptions"),
            "total_errors": await _fetchval(conn, "SELECT COUNT(*) FROM transcriptions WHERE error IS NOT NULL"),
            "tx_24h": await _fetchval(conn, "SELECT COUNT(*) FROM transcriptions WHERE created_at > NOW() - INTERVAL '24 hours'"),
            "total_seconds": int(await _fetchval(conn, "SELECT COALESCE(SUM(duration_seconds), 0) FROM transcriptions")),
            "avg_latency_ms": int(await _fetchval(conn, "SELECT COALESCE(AVG(latency_ms), 0) FROM transcriptions WHERE error IS NULL")),
        }


async def hourly_activity(hours: int = 24) -> list[dict]:
    try:
        async with pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    date_trunc('hour', created_at) AS hour,
                    COUNT(*) AS cnt,
                    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
                FROM transcriptions
                WHERE created_at > NOW() - make_interval(hours => $1)
                GROUP BY 1 ORDER BY 1
                """,
                hours,
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"hourly_activity failed: {e}")
        return []


async def list_users(limit: int = 50, offset: int = 0, search: str = "") -> list[dict]:
    async with pool().acquire() as conn:
        if search:
            rows = await conn.fetch(
                """
                SELECT u.*, COUNT(t.id) AS voice_count,
                       COALESCE(SUM(t.duration_seconds), 0) AS total_seconds
                FROM users u
                LEFT JOIN transcriptions t ON t.user_id = u.telegram_id
                WHERE u.username ILIKE $1 OR u.first_name ILIKE $1 OR CAST(u.telegram_id AS TEXT) LIKE $1
                GROUP BY u.telegram_id
                ORDER BY u.last_active_at DESC
                LIMIT $2 OFFSET $3
                """,
                f"%{search}%", limit, offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT u.*, COUNT(t.id) AS voice_count,
                       COALESCE(SUM(t.duration_seconds), 0) AS total_seconds
                FROM users u
                LEFT JOIN transcriptions t ON t.user_id = u.telegram_id
                GROUP BY u.telegram_id
                ORDER BY u.last_active_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset,
            )
        return [dict(r) for r in rows]


async def list_transcriptions(limit: int = 50, offset: int = 0, search: str = "", errors_only: bool = False) -> list[dict]:
    where = []
    params: list = []
    if errors_only:
        where.append("t.error IS NOT NULL")
    if search:
        params.append(f"%{search}%")
        where.append(f"(t.raw_text ILIKE ${len(params)} OR t.fixed_text ILIKE ${len(params)})")
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    params.extend([limit, offset])
    sql = f"""
        SELECT t.*, u.username, u.first_name
        FROM transcriptions t
        LEFT JOIN users u ON u.telegram_id = t.user_id
        {where_sql}
        ORDER BY t.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]


async def count_users(search: str = "") -> int:
    async with pool().acquire() as conn:
        if search:
            return await conn.fetchval(
                """
                SELECT COUNT(*) FROM users
                WHERE username ILIKE $1 OR first_name ILIKE $1 OR CAST(telegram_id AS TEXT) LIKE $1
                """,
                f"%{search}%",
            )
        return await conn.fetchval("SELECT COUNT(*) FROM users")


async def count_transcriptions(search: str = "", errors_only: bool = False) -> int:
    where = []
    params: list = []
    if errors_only:
        where.append("error IS NOT NULL")
    if search:
        params.append(f"%{search}%")
        where.append(f"(raw_text ILIKE ${len(params)} OR fixed_text ILIKE ${len(params)})")
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sql = f"SELECT COUNT(*) FROM transcriptions {where_sql}"
    async with pool().acquire() as conn:
        return await conn.fetchval(sql, *params)

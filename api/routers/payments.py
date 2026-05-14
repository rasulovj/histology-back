from fastapi import APIRouter, Depends, HTTPException
from api.auth import verify_token
import aiohttp
import aiosqlite
import os
from datetime import datetime, timedelta, timezone
from services.user_service import DB_PATH

router = APIRouter()
SOFPAY_BASE = "https://sofpay.uz/api/v1"


async def _ensure_tables():
    """Create tables if they don't exist yet (in case bot hasn't restarted)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                tx_id TEXT UNIQUE,
                amount INTEGER,
                paid_at TEXT,
                payment_type TEXT DEFAULT 'premium'
            )
        """)
        try:
            await db.execute("ALTER TABLE payment_history ADD COLUMN payment_type TEXT DEFAULT 'premium'")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE pending_payments ADD COLUMN payment_type TEXT DEFAULT 'premium'")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


def _shop_key() -> str:
    key = os.getenv("SOFPAY_SHOP_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="SOFPAY_SHOP_KEY not configured")
    return key


async def _local_history(limit: int = 500) -> list:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT ph.id, ph.user_id, ph.tx_id, ph.amount, ph.paid_at,
                      COALESCE(ph.payment_type, 'premium') as payment_type,
                      u.fio, u.faculty, u.course
               FROM payment_history ph
               LEFT JOIN users u ON u.user_id = ph.user_id
               ORDER BY ph.paid_at DESC
               LIMIT ?""",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "id": r[0], "user_id": r[1], "tx_id": r[2],
            "amount": r[3], "paid_at": r[4],
            "payment_type": r[5], "fio": r[6], "faculty": r[7], "course": r[8],
            "status": "paid",
        }
        for r in rows
    ]


async def _pending_with_users() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT pp.user_id, pp.tx_id, pp.amount, pp.created_at,
                      COALESCE(pp.payment_type, 'premium') as payment_type,
                      u.fio, u.faculty, u.course
               FROM pending_payments pp
               LEFT JOIN users u ON u.user_id = pp.user_id
               ORDER BY pp.created_at DESC"""
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "user_id": r[0], "tx_id": r[1], "amount": r[2], "paid_at": r[3],
            "payment_type": r[4], "fio": r[5], "faculty": r[6], "course": r[7],
            "status": "pending",
        }
        for r in rows
    ]


def _parse_dt(tx: dict):
    for field in ("paid_at", "created_at", "date", "timestamp"):
        raw = tx.get(field)
        if raw:
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
    return None


@router.get("/stats")
async def payments_stats(_=Depends(verify_token)):
    await _ensure_tables()
    history = await _local_history(1000)
    pending = await _pending_with_users()
    now = datetime.now(timezone.utc)
    cut7  = now - timedelta(days=7)
    cut30 = now - timedelta(days=30)

    def in_window(tx, cut):
        dt = _parse_dt(tx)
        return dt is not None and dt >= cut

    return {
        "total_income":         sum(tx["amount"] for tx in history),
        "income_7d":            sum(tx["amount"] for tx in history if in_window(tx, cut7)),
        "income_30d":           sum(tx["amount"] for tx in history if in_window(tx, cut30)),
        "total_transactions":   len(history) + len(pending),
        "paid_transactions":    len(history),
        "pending_transactions": len(pending),
    }


@router.get("/transactions")
async def list_transactions(limit: int = 200, _=Depends(verify_token)):
    paid    = await _local_history(limit)
    pending = await _pending_with_users()
    combined = paid + pending
    combined.sort(
        key=lambda tx: _parse_dt(tx) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return combined[:limit]


@router.get("/chart-data")
async def chart_data(days: int = 30, _=Depends(verify_token)):
    """Daily income + transaction count for the last N days."""
    await _ensure_tables()
    now = datetime.now(timezone.utc)
    result = []
    async with aiosqlite.connect(DB_PATH) as db:
        for i in range(days - 1, -1, -1):
            day = now - timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            async with db.execute(
                "SELECT COALESCE(SUM(amount),0), COUNT(*) FROM payment_history WHERE paid_at LIKE ?",
                (f"{day_str}%",)
            ) as cur:
                row = await cur.fetchone()
            result.append({
                "date": day_str,
                "income": row[0],
                "count": row[1],
            })
    return result


@router.post("/transactions/{tx_id}/cancel")
async def cancel_transaction(tx_id: str, _=Depends(verify_token)):
    key = _shop_key()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{SOFPAY_BASE}/transaction/{tx_id}/cancel",
            headers={"X-Shop-Key": key, "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

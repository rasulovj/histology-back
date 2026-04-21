from fastapi import APIRouter, Depends, Query
from api.auth import verify_token
import aiosqlite
import math
from services.user_service import DB_PATH

router = APIRouter()


@router.get("")
async def list_feedback(
    type: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: str = Depends(verify_token),
):
    conditions = []
    params = []

    if type:
        conditions.append("f.type = ?")
        params.append(type)
    if date_from:
        conditions.append("f.date >= ?")
        params.append(date_from)
    if date_to:
        # include the full end day
        conditions.append("f.date <= ?")
        params.append(date_to + "T23:59:59")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT COUNT(*) FROM feedback f {where}", params
        ) as cur:
            total = (await cur.fetchone())[0]

        offset = (page - 1) * limit
        async with db.execute(
            f"SELECT f.id, f.user_id, f.type, f.text, f.date, u.fio, u.faculty "
            f"FROM feedback f LEFT JOIN users u ON f.user_id = u.user_id "
            f"{where} ORDER BY f.date DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ) as cur:
            rows = await cur.fetchall()

    items = [
        {
            "id": r[0],
            "user_id": r[1],
            "type": r[2],
            "text": r[3],
            "date": r[4],
            "fio": r[5],
            "faculty": r[6],
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, math.ceil(total / limit)),
    }

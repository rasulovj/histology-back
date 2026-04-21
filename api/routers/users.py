import asyncio
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from api.auth import verify_token
import aiosqlite
import math
from services.user_service import DB_PATH, get_avatar_cache_path, get_avatar_public_url, refresh_user_avatar

router = APIRouter()


def _row_to_user(row) -> dict:
    keys = ["user_id", "fio", "course", "year", "faculty", "lang",
            "activity", "is_premium", "registration_date", "last_active_date", "last_topic"]
    payload = dict(zip(keys, row))
    payload["avatar_url"] = get_avatar_public_url(payload["user_id"])
    return payload


@router.get("")
async def list_users(
    search: str = Query(""),
    course: str = Query(""),
    lang: str = Query(""),
    premium: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: str = Depends(verify_token),
):
    conditions = []
    params = []

    if search:
        conditions.append("(fio LIKE ? OR user_id LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if course:
        conditions.append("course = ?")
        params.append(course)
    if lang:
        conditions.append("lang = ?")
        params.append(lang)
    if premium != "":
        conditions.append("is_premium = ?")
        params.append(int(premium))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT COUNT(*) FROM users {where}", params
        ) as cur:
            total = (await cur.fetchone())[0]

        offset = (page - 1) * limit
        async with db.execute(
            f"SELECT user_id, fio, course, year, faculty, lang, activity, is_premium, "
            f"registration_date, last_active_date, last_topic FROM users {where} "
            f"ORDER BY registration_date DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ) as cur:
            rows = await cur.fetchall()

    await asyncio.gather(*(refresh_user_avatar(str(row[0])) for row in rows), return_exceptions=True)

    return {
        "items": [_row_to_user(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, math.ceil(total / limit)),
    }


@router.get("/{user_id}")
async def get_user(user_id: str, _: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, fio, course, year, faculty, lang, activity, is_premium, "
            "registration_date, last_active_date, last_topic FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    await refresh_user_avatar(user_id)
    return _row_to_user(row)


@router.get("/{user_id}/avatar")
async def get_user_avatar(user_id: str):
    avatar_path = get_avatar_cache_path(user_id)
    if not os.path.exists(avatar_path):
        refreshed = await refresh_user_avatar(user_id, force=True)
        if not refreshed or not os.path.exists(avatar_path):
            raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(avatar_path, media_type="image/jpeg")


class PremiumUpdate(BaseModel):
    is_premium: int


@router.patch("/{user_id}/premium")
async def update_premium(user_id: str, body: PremiumUpdate, _: str = Depends(verify_token)):
    if body.is_premium not in (0, 1):
        raise HTTPException(status_code=422, detail="is_premium must be 0, 1, or 2")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="User not found")
        await db.execute(
            "UPDATE users SET is_premium = ? WHERE user_id = ?", (body.is_premium, user_id)
        )
        await db.commit()

    return await get_user(user_id, _)


@router.delete("/{user_id}")
async def delete_user(user_id: str, _: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="User not found")
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()
    return {"deleted": True}

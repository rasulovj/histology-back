from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.auth import verify_token
import aiosqlite
from services.user_service import DB_PATH

router = APIRouter()


class AdminCreate(BaseModel):
    user_id: str
    role: str
    department: Optional[str] = None


@router.get("")
async def list_admins(_: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, role, department FROM admins") as cur:
            rows = await cur.fetchall()
    return [{"user_id": r[0], "role": r[1], "department": r[2]} for r in rows]


@router.post("")
async def create_admin(body: AdminCreate, _: str = Depends(verify_token)):
    if body.role not in ("superadmin", "department"):
        raise HTTPException(status_code=422, detail="role must be 'superadmin' or 'department'")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO admins (user_id, role, department) VALUES (?, ?, ?)",
            (str(body.user_id), body.role, body.department or "general"),
        )
        await db.commit()

    return {"user_id": body.user_id, "role": body.role, "department": body.department or "general"}


@router.delete("/{user_id}")
async def delete_admin(user_id: str, _: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        # Guard: cannot delete last admin
        async with db.execute("SELECT COUNT(*) FROM admins") as cur:
            count = (await cur.fetchone())[0]
        if count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin")

        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="Admin not found")

        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()

    return {"deleted": True}

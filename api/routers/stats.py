from fastapi import APIRouter, Depends
from api.auth import verify_token
import aiosqlite
from datetime import datetime, timedelta
from services.user_service import DB_PATH

router = APIRouter()

# All known faculty names in every language → canonical key
_FACULTY_ALIASES: dict[str, str] = {
    # RU
    "Лечебное дело": "fac_lech",
    "Педиатрия": "fac_ped",
    "Стоматология": "fac_stom",
    "Мед. педагогика": "fac_medped",
    "Мед. профилактика": "fac_medprof",
    "Фармация": "fac_farm",
    "ВМСО (Медсестры)": "fac_nurse",
    "Воен. медицина": "fac_mil",
    "Международный": "fac_inter",
    "Ординатура/Магистратура": "fac_postgrad",
    # EN
    "General Medicine": "fac_lech",
    "Pediatrics": "fac_ped",
    "Dentistry": "fac_stom",
    "Medical Pedagogy": "fac_medped",
    "Medical Prevention": "fac_medprof",
    "Pharmacy": "fac_farm",
    "Higher Nursing": "fac_nurse",
    "Military Medicine": "fac_mil",
    "International": "fac_inter",
    "Residency/Masters": "fac_postgrad",
    # UZ
    "Davolash ishi": "fac_lech",
    "Pediatriya": "fac_ped",
    "Stomatologiya": "fac_stom",
    "Tibbiy pedagogika": "fac_medped",
    "Tibbiy profilaktika": "fac_medprof",
    "Farmatsiya": "fac_farm",
    "OMH (Hamshiralik)": "fac_nurse",
    "Harbiy tibbiyot": "fac_mil",
    "Xalqaro fakultet": "fac_inter",
    "Ordinatura/Magistratura": "fac_postgrad",
}

def _normalize_faculty(raw: str) -> str:
    """Return canonical faculty key, or the raw value if unknown."""
    return _FACULTY_ALIASES.get(raw, raw)


@router.get("/overview")
async def get_overview(_: str = Depends(verify_token)):
    today = datetime.now().strftime('%Y-%m-%d')
    week_start = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
    month_start = (datetime.now() - timedelta(days=29)).strftime('%Y-%m-%d')

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE last_active_date = ?", (today,)
        ) as cur:
            dau = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE last_active_date >= ?", (week_start,)
        ) as cur:
            wau = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE last_active_date >= ?", (month_start,)
        ) as cur:
            mau = (await cur.fetchone())[0]

    return {"total_users": total, "dau": dau, "wau": wau, "mau": mau}


@router.get("/new-users")
async def get_new_users(days: int = 30, _: str = Depends(verify_token)):
    if days not in (7, 30, 90):
        days = 30

    result = []
    async with aiosqlite.connect(DB_PATH) as db:
        for i in range(days - 1, -1, -1):
            date_str = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE registration_date = ?", (date_str,)
            ) as cur:
                count = (await cur.fetchone())[0]
            result.append({"date": date_str, "count": count})

    return result


@router.get("/by-course")
async def get_by_course(_: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT course, COUNT(*) FROM users GROUP BY course ORDER BY CAST(course AS INTEGER)"
        ) as cur:
            rows = await cur.fetchall()
    return [{"course": str(r[0]), "count": r[1]} for r in rows]


@router.get("/by-faculty")
async def get_by_faculty(_: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT faculty, COUNT(*) FROM users GROUP BY faculty ORDER BY COUNT(*) DESC"
        ) as cur:
            rows = await cur.fetchall()

    # Merge rows that map to the same canonical key
    merged: dict[str, int] = {}
    for raw_faculty, count in rows:
        key = _normalize_faculty(str(raw_faculty or ""))
        merged[key] = merged.get(key, 0) + count

    return sorted(
        [{"faculty": k, "count": v} for k, v in merged.items()],
        key=lambda x: x["count"],
        reverse=True,
    )


@router.get("/by-lang")
async def get_by_lang(_: str = Depends(verify_token)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT lang, COUNT(*) FROM users GROUP BY lang"
        ) as cur:
            rows = await cur.fetchall()
    return [{"lang": str(r[0]), "count": r[1]} for r in rows]

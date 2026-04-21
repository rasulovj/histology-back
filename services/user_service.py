import os
import shutil
import json
import aiosqlite
import aiohttp
from datetime import datetime, timedelta
from typing import Optional
from collections import Counter
from config import ADMIN_ID, BASE_DIR, BOT_TOKEN

DB_PATH = os.path.join(BASE_DIR, "data", "users.db")
AVATAR_DIR = os.path.join(BASE_DIR, "data", "telegram_avatars")

def _legacy_db_candidates() -> list[str]:
    raw = [
        os.path.abspath(os.path.join("data", "users.db")),
        os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), "data", "users.db")),
        os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(BASE_DIR)), "data", "users.db")),
        os.path.join(BASE_DIR, "init_data", "users.db"),
        os.path.join(os.path.expanduser("~"), "data", "users.db"),
    ]
    unique = []
    seen = set()
    primary = os.path.abspath(DB_PATH)
    for path in raw:
        ap = os.path.abspath(path)
        if ap == primary or ap in seen:
            continue
        seen.add(ap)
        if os.path.exists(ap):
            unique.append(ap)
    return unique

async def init_db():
    if not os.path.exists(DB_PATH):
        legacy_paths = {
            os.path.abspath(os.path.join("data", "users.db")),
            os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), "data", "users.db")),
        }
        for legacy in legacy_paths:
            if legacy != os.path.abspath(DB_PATH) and os.path.exists(legacy):
                os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                shutil.copy2(legacy, DB_PATH)
                break

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                fio TEXT,
                course TEXT,
                year TEXT,
                faculty TEXT,
                lang TEXT,
                activity INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0
            )
        """)
        
        # Миграции
        columns_to_add = [
            ("is_premium", "INTEGER DEFAULT 0"),
            ("registration_date", "TEXT"),
            ("last_active_date", "TEXT"),
            ("last_topic", "TEXT"),
            ("avatar_file_id", "TEXT"),
            ("avatar_updated_at", "TEXT"),
        ]
        
        for col_name, col_type in columns_to_add:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except aiosqlite.OperationalError:
                pass

        # Request-limit tracking
        for col_name, col_type in [
            ("daily_requests", "INTEGER DEFAULT 0"),
            ("last_request_date", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except aiosqlite.OperationalError:
                pass
                
        # Pending payments
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_payments (
                user_id TEXT PRIMARY KEY,
                tx_id TEXT,
                amount INTEGER,
                created_at TEXT
            )
        """)

        # Таблица администраторов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id TEXT PRIMARY KEY,
                role TEXT,
                department TEXT
            )
        """)
        
        # Таблица отзывов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                type TEXT,
                text TEXT,
                date TEXT
            )
        """)
        
        # Payment history (permanent record of all completed payments)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                tx_id TEXT UNIQUE,
                amount INTEGER,
                paid_at TEXT
            )
        """)

        # Admin-managed control tests
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_filename TEXT,
                is_active INTEGER DEFAULT 0,
                created_by TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS test_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                correct_indices_json TEXT NOT NULL,
                image_path TEXT,
                created_at TEXT,
                FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE
            )
        """)

        # Главный админ
        await db.execute("""
            INSERT OR IGNORE INTO admins (user_id, role, department)
            VALUES (?, 'superadmin', 'all')
        """, (str(ADMIN_ID),))

        await db.commit()

# --- АДМИН ПАНЕЛЬ ---

async def get_admin_role(user_id):
    if str(user_id) == str(ADMIN_ID):
        return "superadmin"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT role FROM admins WHERE user_id = ?", (str(user_id),)) as cursor:
            row = await cursor.fetchone()
            if row: return row[0]
    return None

async def add_admin(user_id, role, department="general"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO admins (user_id, role, department) 
            VALUES (?, ?, ?)
        """, (str(user_id), role, department))
        await db.commit()

async def remove_admin(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (str(user_id),))
        await db.commit()

# --- ПОЛЬЗОВАТЕЛИ ---

async def is_user_registered(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (str(user_id),)) as cursor:
            return await cursor.fetchone() is not None

async def get_user_profile(user_id):
    defaults = {
        "fio": "Student", "course": "1", "year": "2024",
        "faculty": "Unknown", "lang": "ru", "activity": 0
    }
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),)) as cursor:
            row = await cursor.fetchone()
            if row: return dict(row)
            return defaults

async def save_user_profile(user_id, data_dict):
    today = datetime.now().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        current_activity = 0
        is_premium = 0
        registration_date = today
        last_topic = ""
        daily_requests = 0
        last_request_date = None
        
        async with db.execute(
            "SELECT activity, is_premium, registration_date, last_topic, COALESCE(daily_requests, 0), last_request_date "
            "FROM users WHERE user_id = ?",
            (str(user_id),)
        ) as cursor:
            row = await cursor.fetchone()
            if row: 
                current_activity = row[0]
                is_premium = row[1]
                registration_date = row[2] if row[2] else today
                last_topic = row[3]
                daily_requests = row[4]
                last_request_date = row[5]

        await db.execute("""
            INSERT OR REPLACE INTO users (
                user_id, fio, course, year, faculty, lang, activity, is_premium,
                registration_date, last_active_date, last_topic, daily_requests, last_request_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(user_id),
            data_dict.get('fio', 'Student'),
            data_dict.get('course', '1'),
            data_dict.get('year', '2024'),
            data_dict.get('faculty', 'Unknown'),
            data_dict.get('lang', 'ru'),
            current_activity,
            is_premium,
            registration_date,
            today,
            last_topic,
            daily_requests,
            last_request_date
        ))
        await db.commit()

async def _set_avatar_cache_state(user_id: str, file_id: Optional[str], updated_at: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET avatar_file_id = ?, avatar_updated_at = ? WHERE user_id = ?",
            (file_id, updated_at, str(user_id)),
        )
        await db.commit()

def get_avatar_cache_path(user_id: str) -> str:
    os.makedirs(AVATAR_DIR, exist_ok=True)
    return os.path.join(AVATAR_DIR, f"{user_id}.jpg")

def get_avatar_public_url(user_id: str) -> Optional[str]:
    avatar_path = get_avatar_cache_path(str(user_id))
    if os.path.exists(avatar_path):
        return f"/api/users/{user_id}/avatar"
    return None

async def refresh_user_avatar(user_id: str, force: bool = False) -> bool:
    if not BOT_TOKEN:
        return False

    user_id = str(user_id)
    avatar_path = get_avatar_cache_path(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT avatar_file_id, avatar_updated_at FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

    avatar_file_id = row[0] if row else None
    avatar_updated_at = row[1] if row else None
    if not force and avatar_file_id and avatar_updated_at and os.path.exists(avatar_path):
        try:
            cached_at = datetime.fromisoformat(avatar_updated_at)
            if datetime.utcnow() - cached_at < timedelta(hours=24):
                return True
        except ValueError:
            pass

    api_root = f"https://api.telegram.org/bot{BOT_TOKEN}"
    file_root = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{api_root}/getUserProfilePhotos",
                params={"user_id": user_id, "limit": 1},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                payload = await response.json()

            photos = payload.get("result", {}).get("photos", []) if payload.get("ok") else []
            if not photos:
                if os.path.exists(avatar_path):
                    os.remove(avatar_path)
                await _set_avatar_cache_state(user_id, None, None)
                return False

            file_id = photos[0][-1]["file_id"]
            async with session.get(
                f"{api_root}/getFile",
                params={"file_id": file_id},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                file_payload = await response.json()

            file_path = file_payload.get("result", {}).get("file_path") if file_payload.get("ok") else None
            if not file_path:
                return False

            async with session.get(
                f"{file_root}/{file_path}",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    return False
                content = await response.read()

        with open(avatar_path, "wb") as avatar_file:
            avatar_file.write(content)
        await _set_avatar_cache_state(user_id, file_id, datetime.utcnow().isoformat())
        return True
    except Exception:
        return os.path.exists(avatar_path)

async def update_user_activity(user_id):
    today = datetime.now().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET activity = activity + 1, last_active_date = ? WHERE user_id = ?", (today, str(user_id)))
        await db.commit()

async def update_user_lang(user_id, lang):
    today = datetime.now().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (str(user_id),)) as cursor:
            exists = await cursor.fetchone()
            if exists:
                await db.execute("UPDATE users SET lang = ?, last_active_date = ? WHERE user_id = ?", (lang, today, str(user_id)))
            else:
                await db.execute("INSERT INTO users (user_id, lang, course, fio, registration_date, last_active_date) VALUES (?, ?, '1', 'Guest', ?, ?)", (str(user_id), lang, today, today))
        await db.commit()

async def get_bot_statistics():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]
            
        courses_dict = {}
        async with db.execute("SELECT course, COUNT(*) FROM users GROUP BY course ORDER BY CAST(course AS INTEGER)") as cursor:
            for row in await cursor.fetchall():
                courses_dict[str(row[0])] = row[1]
                
        langs_dict = {}
        async with db.execute("SELECT lang, COUNT(*) FROM users GROUP BY lang") as cursor:
            for row in await cursor.fetchall():
                langs_dict[str(row[0])] = row[1]
            
        return {"total": total_users, "courses": courses_dict, "langs": langs_dict}

async def get_admin_statistics():
    today = datetime.now().strftime('%Y-%m-%d')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM users WHERE last_active_date = ?", (today,)) as cursor:
            dau = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM users WHERE last_active_date >= ?", (week_ago,)) as cursor:
            wau = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM users WHERE registration_date >= ?", (week_ago,)) as cursor:
            new_this_week = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT COUNT(*) FROM users WHERE activity > 1 AND last_active_date != registration_date") as cursor:
            retention = (await cursor.fetchone())[0]

        return {"total": total, "dau": dau, "wau": wau, "new_weekly": new_this_week, "retention": retention}

async def get_user_lang(user_id):
    profile = await get_user_profile(user_id)
    return profile.get("lang", "ru")

async def get_user_course(user_id):
    profile = await get_user_profile(user_id)
    return profile.get("course", "1")

# --- БЫСТРЫЕ КНОПКИ (LAST TOPIC) ---

async def update_last_topic(user_id, topic):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_topic = ? WHERE user_id = ?", (topic, str(user_id)))
        await db.commit()

async def get_last_topic(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_topic FROM users WHERE user_id = ?", (str(user_id),)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

# --- МОНЕТИЗАЦИЯ ---

FREE_DAILY_LIMIT = 3

def _is_premium_active(value) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return str(value).strip().lower() in {"true", "yes", "premium", "active"}

async def set_user_premium(user_id, level=1):
    today = datetime.now().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO users (
                user_id, fio, course, year, faculty, lang, activity, is_premium,
                registration_date, last_active_date, last_topic, daily_requests, last_request_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(user_id), "Guest", "1", "2024", "Unknown", "ru", 0, 0, today, today, "", 0, today)
        )
        await db.execute(
            "UPDATE users SET is_premium = ?, daily_requests = 0 WHERE user_id = ?",
            (level, str(user_id))
        )
        await db.commit()

async def _promote_user_in_primary_db(user_id):
    uid = str(user_id)
    today = datetime.now().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO users (
                user_id, fio, course, year, faculty, lang, activity, is_premium,
                registration_date, last_active_date, last_topic, daily_requests, last_request_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, "Guest", "1", "2024", "Unknown", "ru", 0, 1, today, today, "", 0, today)
        )
        await db.execute(
            "UPDATE users SET is_premium = 1, daily_requests = 0 WHERE user_id = ?",
            (uid,)
        )
        await db.commit()

async def _is_user_premium_in_db(db_path: str, user_id) -> bool:
    uid = str(user_id)
    try:
        async with aiosqlite.connect(db_path) as db:
            try:
                async with db.execute("SELECT is_premium FROM users WHERE user_id = ?", (uid,)) as cursor:
                    row = await cursor.fetchone()
                    if row and _is_premium_active(row[0]):
                        return True
            except aiosqlite.OperationalError:
                pass

            try:
                async with db.execute(
                    "SELECT COUNT(1) FROM payment_history WHERE user_id = ?",
                    (uid,)
                ) as cursor:
                    return ((await cursor.fetchone())[0] or 0) > 0
            except aiosqlite.OperationalError:
                return False
    except Exception:
        return False

async def get_user_premium_status(user_id):
    uid = str(user_id)
    today = datetime.now().strftime('%Y-%m-%d')
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_premium FROM users WHERE user_id = ?", (uid,)) as cursor:
            row = await cursor.fetchone()
        stored_value = row[0] if row else 0
        if _is_premium_active(stored_value):
            return 1

        async with db.execute(
            "SELECT COUNT(1) FROM payment_history WHERE user_id = ?",
            (uid,)
        ) as cursor:
            has_paid_history = ((await cursor.fetchone())[0] or 0) > 0

        if has_paid_history:
            await db.execute(
                """
                INSERT OR IGNORE INTO users (
                    user_id, fio, course, year, faculty, lang, activity, is_premium,
                    registration_date, last_active_date, last_topic, daily_requests, last_request_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, "Guest", "1", "2024", "Unknown", "ru", 0, 1, today, today, "", 0, today)
            )
            await db.execute(
                "UPDATE users SET is_premium = 1, daily_requests = 0 WHERE user_id = ?",
                (uid,)
            )
            await db.commit()
            return 1

        for legacy_db in _legacy_db_candidates():
            if await _is_user_premium_in_db(legacy_db, uid):
                await _promote_user_in_primary_db(uid)
                return 1

        return 0

async def check_and_increment_requests(user_id) -> tuple[bool, int]:
    """Returns (allowed, remaining). Increments lifetime counter if allowed."""
    if _is_premium_active(await get_user_premium_status(user_id)):
        return True, -1

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(daily_requests, 0) FROM users WHERE user_id = ?",
            (str(user_id),)
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            today = datetime.now().strftime('%Y-%m-%d')
            await db.execute(
                """
                INSERT INTO users (
                    user_id, fio, course, year, faculty, lang, activity, is_premium,
                    registration_date, last_active_date, last_topic, daily_requests, last_request_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(user_id), "Guest", "1", "2024", "Unknown", "ru", 0, 0, today, today, "", 1, today)
            )
            await db.commit()
            return True, FREE_DAILY_LIMIT - 1

        used = row[0] if row else 0

        if used >= FREE_DAILY_LIMIT:
            return False, 0

        new_count = used + 1
        today = datetime.now().strftime('%Y-%m-%d')
        await db.execute(
            "UPDATE users SET daily_requests = ?, last_request_date = ? WHERE user_id = ?",
            (new_count, today, str(user_id))
        )
        await db.commit()
        return True, FREE_DAILY_LIMIT - new_count

async def save_pending_payment(user_id, tx_id: str, amount: int):
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO pending_payments (user_id, tx_id, amount, created_at) VALUES (?, ?, ?, ?)",
            (str(user_id), tx_id, amount, date)
        )
        await db.commit()

async def get_pending_payment(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tx_id, amount FROM pending_payments WHERE user_id = ?",
            (str(user_id),)
        ) as cursor:
            row = await cursor.fetchone()
            return {"tx_id": row[0], "amount": row[1]} if row else None

async def delete_pending_payment(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_payments WHERE user_id = ?", (str(user_id),))
        await db.commit()

async def record_payment(user_id, tx_id: str, amount: int):
    """Permanently record a completed payment to history."""
    paid_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO payment_history (user_id, tx_id, amount, paid_at) VALUES (?, ?, ?, ?)",
            (str(user_id), tx_id, amount, paid_at)
        )
        await db.commit()

async def get_payment_history(limit: int = 200) -> list:
    """Return all payment history records joined with user info."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT ph.id, ph.user_id, ph.tx_id, ph.amount, ph.paid_at,
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
            "fio": r[5], "faculty": r[6], "course": r[7],
        }
        for r in rows
    ]

# --- ОТЗЫВЫ (FEEDBACK) ---

async def save_feedback(user_id, fb_type, text):
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO feedback (user_id, type, text, date) VALUES (?, ?, ?, ?)", (str(user_id), fb_type, text, date))
        await db.commit()

async def get_all_feedback():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT f.id, f.type, f.text, f.date, u.fio, u.faculty 
            FROM feedback f 
            LEFT JOIN users u ON f.user_id = u.user_id 
            ORDER BY f.id DESC
        """) as cursor:
            return await cursor.fetchall()

async def get_all_users_for_broadcast():
    """Возвращает список всех пользователей и их язык для рассылки"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id, lang FROM users") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


def _parse_json_list(raw: Optional[str]) -> list:
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _normalize_control_test_questions(rows) -> list[dict]:
    questions = []
    for row in rows:
        options = [str(item).strip() for item in _parse_json_list(row[1]) if str(item).strip()]
        correct_indices = []
        for item in _parse_json_list(row[2]):
            try:
                parsed = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= parsed < len(options) and parsed not in correct_indices:
                correct_indices.append(parsed)

        question_text = str(row[0] or "").strip()
        if not question_text:
            continue

        if options and not correct_indices:
            questions.append(
                {
                    "question": question_text,
                    "question_type": "open",
                    "accepted_answers": options,
                    "options": [],
                    "correct_indices": [],
                    "image_path": row[3],
                    "explanation": "",
                }
            )
            continue

        if len(options) < 2 or not correct_indices:
            continue

        questions.append(
            {
                "question": question_text,
                "question_type": "choice",
                "accepted_answers": [],
                "options": options,
                "correct_indices": correct_indices,
                "image_path": row[3],
                "explanation": "",
            }
        )
    return questions


async def list_active_control_tests() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT t.id, t.title, t.updated_at, COUNT(q.id) AS question_count
            FROM tests t
            LEFT JOIN test_questions q ON q.test_id = t.id
            WHERE t.is_active = 1
            GROUP BY t.id
            ORDER BY t.updated_at DESC, t.id DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "updated_at": row[2],
            "question_count": row[3],
        }
        for row in rows
        if (row[3] or 0) > 0
    ]


async def get_control_test_by_id(test_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT id, title
            FROM tests
            WHERE id = ? AND is_active = 1
            LIMIT 1
            """,
            (test_id,),
        ) as cursor:
            test_row = await cursor.fetchone()

        if not test_row:
            return None

        async with db.execute(
            """
            SELECT question, options_json, correct_indices_json, image_path
            FROM test_questions
            WHERE test_id = ?
            ORDER BY position ASC
            """,
            (test_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    questions = _normalize_control_test_questions(rows)
    if not questions:
        return None

    return {"id": test_row[0], "title": test_row[1], "questions": questions}

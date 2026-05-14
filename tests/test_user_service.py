import os
import tempfile
import unittest
from datetime import datetime, timedelta

import aiosqlite

import services.user_service as user_service


class PremiumExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = user_service.DB_PATH
        user_service.DB_PATH = os.path.join(self.tmp.name, "users.db")
        await user_service.init_db()

    async def asyncTearDown(self):
        user_service.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def _insert_user(self, user_id: str, is_premium: int = 0):
        today = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(user_service.DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO users (
                    user_id, fio, course, year, faculty, lang, activity, is_premium,
                    registration_date, last_active_date, last_topic, daily_requests, last_request_date
                )
                VALUES (?, 'Student', '1', '2024', 'Unknown', 'en', 0, ?, ?, ?, '', 0, ?)
                """,
                (user_id, is_premium, today, today, today),
            )
            await db.commit()

    async def _insert_payment(self, user_id: str, paid_at: datetime, payment_type: str = "premium"):
        async with aiosqlite.connect(user_service.DB_PATH) as db:
            await db.execute(
                "INSERT INTO payment_history (user_id, tx_id, amount, paid_at, payment_type) VALUES (?, ?, ?, ?, ?)",
                (user_id, f"tx-{paid_at.timestamp()}-{payment_type}", 10000, paid_at.strftime("%Y-%m-%d %H:%M"), payment_type),
            )
            await db.commit()

    async def test_recent_payment_is_active_premium(self):
        user_id = "recent-user"
        await self._insert_user(user_id)
        await self._insert_payment(user_id, datetime.now() - timedelta(days=29))

        status = await user_service.get_user_premium_status(user_id)

        self.assertEqual(status, 1)

    async def test_expired_payment_removes_paid_premium(self):
        user_id = "expired-user"
        await self._insert_user(user_id, is_premium=1)
        await self._insert_payment(user_id, datetime.now() - timedelta(days=31))

        status = await user_service.get_user_premium_status(user_id)

        self.assertEqual(status, 0)

        async with aiosqlite.connect(user_service.DB_PATH) as db:
            async with db.execute("SELECT is_premium FROM users WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
        self.assertEqual(row[0], 0)

    async def test_manual_premium_without_payment_history_stays_active(self):
        user_id = "manual-user"
        await self._insert_user(user_id, is_premium=1)

        status = await user_service.get_user_premium_status(user_id)

        self.assertEqual(status, 1)

    async def test_narozat_payment_does_not_activate_premium(self):
        user_id = "narozat-only-user"
        await self._insert_user(user_id)
        await self._insert_payment(user_id, datetime.now() - timedelta(days=1), payment_type="narozat")

        status = await user_service.get_user_premium_status(user_id)

        self.assertEqual(status, 0)

    async def test_recent_narozat_payment_grants_access(self):
        user_id = "narozat-active"
        await self._insert_user(user_id)
        await self._insert_payment(user_id, datetime.now() - timedelta(days=29), payment_type="narozat")

        self.assertTrue(await user_service.has_active_narozat_access(user_id))

    async def test_expired_narozat_payment_revokes_access(self):
        user_id = "narozat-expired"
        await self._insert_user(user_id)
        await self._insert_payment(user_id, datetime.now() - timedelta(days=31), payment_type="narozat")

        self.assertFalse(await user_service.has_active_narozat_access(user_id))


if __name__ == "__main__":
    unittest.main()

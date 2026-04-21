import os
import shutil
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN, BASE_DIR
from handlers import user_handlers, admin_handlers
from services.user_service import init_db

logging.basicConfig(level=logging.INFO)

def restore_backup():
    """Копирует старую базу и книги в несгораемый Volume при первом запуске"""
    data_dir = os.path.join(BASE_DIR, "data")
    init_data_dir = os.path.join(BASE_DIR, "init_data")
    os.makedirs(data_dir, exist_ok=True)

    # Восстанавливаем базу данных (старых студентов и статистику)
    users_db = os.path.join(data_dir, "users.db")
    init_users_db = os.path.join(init_data_dir, "users.db")
    if not os.path.exists(users_db) and os.path.exists(init_users_db):
        shutil.copy2(init_users_db, users_db)
        print("✅ Старая база данных успешно перенесена в безопасный Volume!")

    # Восстанавливаем библиотеку (старые PDF книги)
    kb_dir = os.path.join(data_dir, "knowledge_base")
    init_kb_dir = os.path.join(init_data_dir, "knowledge_base")
    if not os.path.exists(kb_dir) and os.path.exists(init_kb_dir):
        shutil.copytree(init_kb_dir, kb_dir)
        print("✅ Старые книги успешно перенесены в безопасный Volume!")


async def start_api(bot: Bot):
    import uvicorn
    from api.app import app
    app.state.bot = bot
    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except Exception as e:
        print(f"⚠️  Admin API failed to start: {e}")


async def main():
    # Сначала проверяем и восстанавливаем файлы, если нужно
    restore_backup()

    # Инициализация асинхронной базы данных
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # 🛑 ВАЖНО: Роутер админки должен подключаться ПЕРВЫМ!
    dp.include_router(admin_handlers.router)

    # Роутер обычных пользователей подключаем ВТОРЫМ
    dp.include_router(user_handlers.router)

    await bot.delete_webhook(drop_pending_updates=True)

    print("🚀 AI Study Assistant запущен и готов к HIGHLOAD нагрузкам!")
    print("🌐 Admin API запускается на порту 8000...")

    await asyncio.gather(
        dp.start_polling(bot),
        start_api(bot),
        return_exceptions=True,
    )

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Бот остановлен!")

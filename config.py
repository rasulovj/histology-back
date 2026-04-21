import os
from dotenv import load_dotenv

# Загружаем .env рядом с файлом, чтобы запуск через systemd/cron тоже корректно подхватывал переменные
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


BOT_TOKEN = _require_env("BOT_TOKEN")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY")
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")  # Токен от Click или Payme
ADMIN_ID = int(_require_env("ADMIN_ID"))

# Пути к папкам
DB_PATH = os.path.join(BASE_DIR, "data", "knowledge_base")
RAW_FILES_PATH = os.path.join(BASE_DIR, "data", "raw_files")

# Создаем папки, если их нет
os.makedirs(DB_PATH, exist_ok=True)
os.makedirs(RAW_FILES_PATH, exist_ok=True)

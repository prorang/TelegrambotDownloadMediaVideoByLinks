import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"

# Корректное название функции — load_dotenv
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("❌ Критическая ошибка: Переменная BOT_TOKEN не найдена ни в системе, ни в файле .env!")
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
ADMIN_ID = os.environ.get("ADMIN_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPEN_AI_API_KEY = os.environ.get("OPEN_AI_API_KEY")

if not BOT_TOKEN:
    raise ValueError("❌ Критическая ошибка: Переменная BOT_TOKEN не найдена ни в системе, ни в файле .env!")
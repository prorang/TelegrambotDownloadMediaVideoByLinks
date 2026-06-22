import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer # Импортируем класс сервера
from aiogram.fsm.storage.memory import MemoryStorage
import config
from downloader import Downloader

local_api_url = os.getenv("TELEGRAM_API_URL")

if local_api_url:
    custom_server = TelegramAPIServer.from_base(local_api_url)
    session = AiohttpSession(api=custom_server, timeout=900)
    print(f"🚀 Бот запущен с использованием локального Bot API: {local_api_url}")
else:
    session = AiohttpSession(timeout=600)
    print("🌍 Бот запущен через официальный сервер Telegram API")

bot = Bot(
    token=config.BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode="HTML")
)

dp = Dispatcher(storage=MemoryStorage())
dn = Downloader()
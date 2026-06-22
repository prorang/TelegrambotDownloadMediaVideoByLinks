from aiogram import Bot, Dispatcher
import config
from downloader import Downloader

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
dn = Downloader()
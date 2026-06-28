import asyncio
import logging
# ДОБАВИЛИ ИМПОРТЫ КЛАССОВ ДЛЯ КОМАНД:
from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from loader import bot, dp
import handlers
import inline_handlers

async def set_bot_commands(bot_instance: Bot):
    """Регистрирует команды в меню Telegram."""
    commands = [
        BotCommand(command="start", description="Запустить бота 👋"),
        BotCommand(command="init_tg", description="Обновить sessionid для Instagram 🔑")
    ]
    # Регистрируем команды для всех пользователей в личных сообщениях
    await bot_instance.set_my_commands(commands=commands, scope=BotCommandScopeAllPrivateChats())
    
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("Бот запускается...")
    
    # ВЫЗЫВАЕМ регистрацию команд перед запуском бота
    await set_bot_commands(bot)
    
    # Пропускаем накопившиеся обновления перед стартом
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Бот остановлен.")
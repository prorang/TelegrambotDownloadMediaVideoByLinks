import asyncio
import logging
from loader import bot, dp
import handlers

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("Бот запускается...")
    
    # Пропускаем накопившиеся обновления перед стартом
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Бот остановлен.")
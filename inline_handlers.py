import logging
from aiogram the types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from loader import dp, dn

logger = logging.getLogger(__name__)

@dp.inline_query()
async def inline_link_handler(inline_query: InlineQuery) -> None:
    text = inline_query.query.strip()
    
    # Если строка пустая или не содержит ссылку — ничего не предлагаем
    if not text or not ("http://" in text or "https://" in text):
        return

    # Проверяем платформу через наш downloader
    platform = dn._detect_platform(text)
    if not platform:
        return

    result_id = f"vid_{hash(text)}"
    
    # Формируем инлайн-карточку
    item = InlineQueryResultArticle(
        id=result_id,
        title=f"📥 Скачать видео из {platform.capitalize()}",
        description=text[:50] + "...",
        input_message_content=InputTextMessageContent(
            message_text=text
        )
    )

    try:
        await inline_query.answer(results=[item], cache_time=1)
    except Exception as e:
        logger.error(f"Ошибка отправки инлайн-ответа: {e}")
from pathlib import Path
from aiogram import F, Bot
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.exceptions import TelegramBadRequest

from loader import dp, dn

@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    await message.answer("👋")

@dp.message(F.text)
async def group_and_private_link_handler(message: Message, bot: Bot) -> None:
    text = message.text.strip()
    
    if not ("http://" in text or "https://" in text):
        return

    # Предварительно проверяем платформу, чтобы зря не тереть сообщения в группах
    platform = dn._detect_platform(text)
    if not platform:
        if message.chat.type == "private":
            await message.answer("❌")
        return

    is_group = message.chat.type in ("group", "supergroup")
    
    if is_group:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass

    status_msg = await message.answer("⏳")

    try:
        # Метод теперь возвращает и путь, и название платформы
        file_path, downloaded_platform = await dn.download_video(text)

        if file_path and Path(file_path).exists():
            caption_text = text[:45] + "..." if len(text) > 45 else text
            
            # Отправляем как видео для воспроизведения в плеере
            await message.answer_video(
                video=FSInputFile(file_path),
                caption=caption_text
            )
            Path(file_path).unlink(missing_ok=True)
        else:
            await message.answer("❌")

    except Exception:
        await message.answer("❌")
    
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass
from pathlib import Path
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, ReactionTypeEmoji, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest

import config

from loader import dp, dn

ADMIN_ID = int(config.ADMIN_ID)

router = Router()

@router.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    await message.answer("👋")

@router.message(Command("init_tg"))
async def cmd_init_cookies(message: Message) -> None:
    # Проверяем, что команду вызвал именно хозяин бота
    if message.from_user.id != ADMIN_ID:
        await message.answer("🛑 У вас нет прав для выполнения этой команды.")
        return

    # Извлекаем sessionid из текста команды
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат команды: <code>/init_tg ваш_sessionid</code>")
        return

    sessionid = args[1].strip()
    cookies_path = Path("/app/cookies.txt")

    try:
        # Содержимое файла строго в формате Netscape (с табами)
        cookie_content = (
            "# Netscape HTTP Cookie File\n"
            f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{sessionid}\n"
        )
        
        # Перезаписываем файл cookies.txt
        cookies_path.write_text(cookie_content, encoding="utf-8")
        
        await message.answer("✅ <b>Куки успешно обновлены!</b>\n\nНовое значение sessionid записано в файл. Можешь пробовать отправлять видео.")
    except Exception as e:
        await message.answer(f"❌ Не удалось сохранить куки:\n<code>{e}</code>")

@router.message(F.text, ~F.text.startswith("/"))
async def group_and_private_link_handler(message: Message, bot: Bot) -> None:
    text = message.text.strip()
    
    if not ("http://" in text or "https://" in text):
        return

    platform = dn._detect_platform(text)
    if not platform:
        if message.chat.type == "private":
            await message.answer("❌ Ссылка не поддерживается или распознана неверно.")
        return

    is_group = message.chat.type in ("group", "supergroup")
    
    success = False
    file_path = None
    error_message = "Неизвестная ошибка"

    # Создаем временный статус загрузки
    status_msg = await message.answer("⏳ Скачиваю...")
    caption_text = text[:45] + "..." if len(text) > 45 else text

    try:
        # 1. Запрашиваем метаданные видео
        meta = await dn.get_video_meta(text, platform)
        width = meta.get('width')
        height = meta.get('height')
        duration = meta.get('duration')

        if duration and duration > 600:
            error_message = "⏱ <b>Видео слишком длинное!</b> Ограничение — до 10 минут."
            print(f"⚠️ Пропущено видео по длительности: {duration} сек.")
            # Выходим из try, чтобы не выполнять download_video
            # status_msg и ошибки обработаются в блоке ниже
            raise ValueError(error_message)
        
        # 2. Скачиваем видеоролик
        try:
            file_path, downloaded_platform = await dn.download_video(text)
        except Exception as download_err:
            file_path = None

        if file_path and Path(file_path).exists():
            width = meta.get('width')
            height = meta.get('height')
            
            await message.answer_video(
                video=FSInputFile(file_path),
                caption=caption_text,
                width=width,
                height=height,
                duration=duration
            )
            success = True

        # 4. ЕСЛИ НЕ ВИДЕО (например, картинка/карусель) и это Инстаграм — скачиваем фото
        elif platform == "instagram":
            photos = await dn.download_instagram_photos(text)
            
            if photos:
                if len(photos) == 1:
                    await message.answer_photo(
                        photo=FSInputFile(photos[0]),
                        caption=caption_text
                    )
                else:
                    # Если это карусель из нескольких фото
                    media_group = [
                        InputMediaPhoto(media=FSInputFile(p), caption=caption_text if i == 0 else "")
                        for i, p in enumerate(photos[:10]) # Ограничение Telegram — до 10 медиа
                    ]
                    await message.answer_media_group(media=media_group)
                
                success = True
                
                # Удаляем скачанные папки с фото
                photo_dir = Path(photos[0]).parent
                for p in photos:
                    Path(p).unlink(missing_ok=True)
                photo_dir.rmdir()

        if success and is_group:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass

    except ValueError as ve:
        error_message = str(ve)
    except Exception as e:
        error_message = f"Ошибка обработки ссылки:\n<code>{str(e)[:300]}</code>"
        print(f"❌ Ошибка: {e}")

    # Удаляем временный файл с диска
    if file_path and Path(file_path).exists():
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception as file_del_err:
            print(f"⚠️ Не удалось удалить файл: {file_del_err}")

    # Удаляем сообщение со статусом "⏳ Скачиваю..."
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Обработка ошибки
    if not success:
        # Ставим грустный смайлик в качестве реакции на сообщение с ссылкой
        try:
            await message.set_reaction(reaction=[ReactionTypeEmoji(emoji="😢")])
        except Exception as react_err:
            print(f"⚠️ Не удалось поставить реакцию: {react_err}")

        # Если это личные сообщения — дополнительно пишем лог ошибки в чат
        if not is_group:
            await message.answer(f"❌ <b>Не удалось обработать ссылку!</b>\n\n{error_message}")
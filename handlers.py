from pathlib import Path
from aiogram import F, Bot
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, ReactionTypeEmoji
from aiogram.exceptions import TelegramBadRequest

import config

from loader import dp, dn

ADMIN_ID = int(config.ADMIN_ID)

@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    await message.answer("👋")

@dp.message(Command("init_tg"))
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

@dp.message(F.text)
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

        # 2. Скачиваем видеоролик
        file_path, downloaded_platform = await dn.download_video(text)

        if file_path and Path(file_path).exists():
            file_size_mb = Path(file_path).stat().st_size / (1024 * 1024)
            print(f" Найдено видео: {file_path}, размер: {file_size_mb:.2f} MB")
            
            try:
                await message.answer_video(
                    video=FSInputFile(file_path),
                    caption=caption_text,
                    width=width,
                    height=height,
                    duration=duration
                )
                success = True
                
                # Если всё успешно скачалось и это группа — удаляем исходное сообщение с ссылкой
                if is_group:
                    try:
                        await message.delete()
                    except TelegramBadRequest:
                        pass
                        
            except Exception as send_err:
                error_message = f"Ошибка отправки видео в Telegram: {send_err}"
                print(f"❌ {error_message}")

    except Exception as e:
        error_message = f"Ошибка скачивания через yt-dlp:\n<code>{str(e)[:300]}</code>"
        print(f"❌ Ошибка при работе с видео: {e}")

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
import asyncio
import time
import uuid
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReactionTypeEmoji,
)

import config
from loader import cleaner, dn, dp

ADMIN_ID = int(config.ADMIN_ID)

router = Router()

# Порог размера (в байтах), после которого нужна ручная подтверждающая кнопка
CONFIRM_SIZE_BYTES = config.CONFIRM_SIZE_MB * 1024 * 1024

# Ссылки, ожидающие подтверждения: key -> {"url": ..., "platform": ...}
PENDING_DOWNLOADS: dict[str, dict] = {}

# Уже скачанные файлы, ожидающие подтверждения отправки (оценка размера могла соврать):
# key -> {"path", "url", "meta", "chat_id", "message_id", "is_group", "created_at"}
PENDING_UPLOADS: dict[str, dict] = {}


def _drop_expired_uploads() -> None:
    """Удаляет файлы, по которым пользователь так и не нажал кнопку."""
    now = time.time()
    for key, pending in list(PENDING_UPLOADS.items()):
        if now - pending.get("created_at", now) > config.UPLOAD_TTL:
            PENDING_UPLOADS.pop(key, None)
            cleaner.remove(pending["path"])


def purge_downloads() -> None:
    """Полная очистка downloads/ при старте бота: подтверждать уже нечего."""
    PENDING_UPLOADS.clear()
    cleaner.sweep(force=True)


async def cleanup_downloads_loop(interval: int = config.CLEANUP_INTERVAL) -> None:
    """Периодическая уборка: снимает просроченные подтверждения и «хвосты»."""
    while True:
        await asyncio.sleep(interval)
        try:
            _drop_expired_uploads()
            cleaner.sweep(protected={p["path"] for p in PENDING_UPLOADS.values()})
        except Exception as e:
            print(f"⚠️ Уборка не удалась: {e}")

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
    status_msg = await message.answer("⏳ Проверяю видео...")

    try:
        meta = await dn.get_video_meta(text, platform)
    except Exception:
        meta = {}

    # 1. Слишком длинные видео не качаем вовсе
    duration = meta.get('duration')
    if duration and duration > 600:
        print(f"⚠️ Пропущено видео по длительности: {duration} сек.")
        try:
            await status_msg.delete()
        except Exception:
            pass
        try:
            await message.set_reaction(reaction=[ReactionTypeEmoji(emoji="😢")])
        except Exception:
            pass
        if not is_group:
            await message.answer("⏱ <b>Видео слишком длинное!</b> Ограничение — до 10 минут.")
        return

    # 2. Крупные видео скачиваем только после подтверждения пользователя
    filesize = meta.get('filesize')
    if filesize and filesize > CONFIRM_SIZE_BYTES:
        key = uuid.uuid4().hex
        PENDING_DOWNLOADS[key] = {"url": text, "platform": platform}

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, скачать", callback_data=f"dl_yes:{key}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"dl_no:{key}"),
        ]])

        try:
            await status_msg.delete()
        except Exception:
            pass

        size_mb = filesize / (1024 * 1024)
        await message.answer(
            f"📦 <b>Видео весит ~{size_mb:.0f} МБ</b> "
            f"(больше лимита {config.CONFIRM_SIZE_MB} МБ).\n\nЗагрузить его?",
            reply_markup=kb,
        )
        return

    await _run_download(message, bot, text, platform, meta, status_msg)


async def _run_download(
    message: Message,
    bot: Bot,
    url: str,
    platform: str,
    meta: dict,
    status_msg: Message,
    *,
    size_confirmed: bool = False,
) -> None:
    is_group = message.chat.type in ("group", "supergroup")

    success = False
    file_path = None
    photo_dir = None
    error_message = "Неизвестная ошибка"

    caption_text = url[:45] + "..." if len(url) > 45 else url

    try:
        # 1. Скачиваем видеоролик
        try:
            file_path, downloaded_platform = await dn.download_video(url)
        except Exception:
            file_path = None

        if file_path and Path(file_path).exists():
            # Страховка: оценка до скачивания могла соврать (неточные метаданные,
            # раздувание файла при перекодировке в libx264). Проверяем реальный размер.
            actual_size = Path(file_path).stat().st_size
            if not size_confirmed and actual_size > CONFIRM_SIZE_BYTES:
                key = uuid.uuid4().hex
                PENDING_UPLOADS[key] = {
                    "path": file_path,
                    "url": url,
                    "meta": meta,
                    "chat_id": message.chat.id,
                    "message_id": message.message_id,
                    "is_group": is_group,
                    "created_at": time.time(),
                }

                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Отправить", callback_data=f"up_yes:{key}"),
                    InlineKeyboardButton(text="❌ Удалить", callback_data=f"up_no:{key}"),
                ]])

                try:
                    await status_msg.delete()
                except Exception:
                    pass

                size_mb = actual_size / (1024 * 1024)
                try:
                    await message.answer(
                        f"📦 <b>Файл получился ~{size_mb:.0f} МБ</b> "
                        f"(больше лимита {config.CONFIRM_SIZE_MB} МБ).\n\nОтправить его?",
                        reply_markup=kb,
                    )
                except Exception:
                    # Вопрос не показался — подчищаем запись, а файл удалит общий блок ниже
                    PENDING_UPLOADS.pop(key, None)
                    raise
                # Файл НЕ удаляем — он нужен для отправки после подтверждения.
                return

            await message.answer_video(
                video=FSInputFile(file_path),
                caption=caption_text,
                width=meta.get('width'),
                height=meta.get('height'),
                duration=meta.get('duration')
            )
            success = True

        # 2. ЕСЛИ НЕ ВИДЕО (например, картинка/карусель) и это Инстаграм — скачиваем фото
        elif platform == "instagram":
            photos = await dn.download_instagram_photos(url)

            if photos:
                photo_dir = Path(photos[0]).parent

                if len(photos) == 1:
                    await message.answer_photo(
                        photo=FSInputFile(photos[0]),
                        caption=caption_text
                    )
                else:
                    # Если это карусель из нескольких фото
                    media_group = [
                        InputMediaPhoto(media=FSInputFile(p), caption=caption_text if i == 0 else "")
                        for i, p in enumerate(photos[:10])  # Ограничение Telegram — до 10 медиа
                    ]
                    await message.answer_media_group(media=media_group)

                success = True

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

    # Удаляем временный файл с диска (вместе с «хвостами» .part/.fXXX)
    if file_path:
        cleaner.remove(file_path)

    # Удаляем папку со скачанными фото (даже если отправка упала)
    if photo_dir is not None:
        cleaner.remove(photo_dir)

    # Удаляем сообщение со статусом
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


@router.callback_query(F.data.startswith("dl_yes:"))
async def cb_download_confirm(callback: CallbackQuery, bot: Bot) -> None:
    """Пользователь согласился скачать крупное видео."""
    key = callback.data.split(":", 1)[1]
    pending = PENDING_DOWNLOADS.pop(key, None)

    if not pending:
        await callback.answer("Ссылка устарела, отправьте её заново.", show_alert=True)
        return

    await callback.answer("Скачиваю...")
    message = callback.message

    # Превращаем сообщение с кнопками в статус загрузки
    try:
        status_msg = await message.edit_text("⏳ Скачиваю...", reply_markup=None)
    except TelegramBadRequest:
        status_msg = message

    await _run_download(
        message,
        bot,
        pending["url"],
        pending["platform"],
        {},
        status_msg,
        size_confirmed=True,
    )


@router.callback_query(F.data.startswith("dl_no:"))
async def cb_download_decline(callback: CallbackQuery) -> None:
    """Пользователь отказался от загрузки."""
    key = callback.data.split(":", 1)[1]
    PENDING_DOWNLOADS.pop(key, None)
    await callback.answer("Загрузка отменена")
    try:
        await callback.message.edit_text("🚫 Загрузка отменена.", reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("up_yes:"))
async def cb_upload_confirm(callback: CallbackQuery, bot: Bot) -> None:
    """Пользователь подтвердил отправку уже скачанного крупного файла."""
    key = callback.data.split(":", 1)[1]
    pending = PENDING_UPLOADS.pop(key, None)

    if not pending:
        await callback.answer("Файл уже отправлен или удалён.", show_alert=True)
        return

    await callback.answer("Отправляю...")
    status_msg = callback.message
    try:
        await status_msg.edit_text("⏳ Отправляю...", reply_markup=None)
    except TelegramBadRequest:
        pass

    file_path = pending["path"]
    url = pending["url"]
    meta = pending["meta"]
    caption_text = url[:45] + "..." if len(url) > 45 else url

    try:
        await bot.send_video(
            chat_id=pending["chat_id"],
            video=FSInputFile(file_path),
            caption=caption_text,
            width=meta.get('width'),
            height=meta.get('height'),
            duration=meta.get('duration'),
        )
        if pending["is_group"]:
            try:
                await bot.delete_message(pending["chat_id"], pending["message_id"])
            except TelegramBadRequest:
                pass
    except Exception as e:
        print(f"❌ Ошибка отправки крупного файла: {e}")
        await bot.send_message(
            pending["chat_id"],
            f"❌ <b>Не удалось отправить файл!</b>\n\n<code>{str(e)[:300]}</code>",
        )
    finally:
        cleaner.remove(file_path)
        # Убираем сообщение «⏳ Отправляю...», как это делает _run_download для статуса
        try:
            await status_msg.delete()
        except Exception:
            pass


@router.callback_query(F.data.startswith("up_no:"))
async def cb_upload_decline(callback: CallbackQuery) -> None:
    """Пользователь отказался отправлять скачанный крупный файл — удаляем его."""
    key = callback.data.split(":", 1)[1]
    pending = PENDING_UPLOADS.pop(key, None)

    if pending:
        cleaner.remove(pending["path"])

    await callback.answer("Файл удалён")
    try:
        await callback.message.edit_text("🚫 Файл удалён.", reply_markup=None)
    except TelegramBadRequest:
        pass
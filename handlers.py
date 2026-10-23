import asyncio
import html
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

# Кнопки у отправленных медиа: key -> {"url", "platform", "comments", "created_at"}
MEDIA_STATE: dict[str, dict] = {}
MEDIA_STATE_TTL = 24 * 60 * 60
COMMENTS_LIMIT = 15

# Человекочитаемые названия платформ для подписи «сколько лайков в источнике»
PLATFORM_NAMES = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
}


def _format_likes(count: int | None) -> str:
    """Текст кнопки с количеством лайков из источника."""
    if count is None:
        return "❤️ —"
    return f"❤️ {count:,}".replace(",", " ")


def _media_kb(
    url: str,
    key: str,
    platform: str | None,
    like_count: int | None,
) -> InlineKeyboardMarkup:
    """Клавиатура под медиа: ссылка на оригинал, лайки источника и комментарии.

    Telegram не переносит inline-клавиатуру при пересылке сообщения, поэтому
    в пересланном видео ссылки не остаётся (в отличие от ссылки в подписи).
    """
    platform_tag = platform or "unknown"
    likes_tag = str(like_count) if like_count is not None else "none"
    likes_button = InlineKeyboardButton(
        text=_format_likes(like_count),
        callback_data=f"likes:{platform_tag}:{likes_tag}",
    )
    actions = [likes_button]
    if dn.supports_comments(platform):
        actions.append(
            InlineKeyboardButton(text="💬 Комментарии", callback_data=f"cmt:{key}")
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылка на оригинал", url=url)],
        actions,
    ])


def _register_media_state(url: str, platform: str | None) -> str:
    """Создаёт состояние кнопок и возвращает короткий ключ для callback_data."""
    key = uuid.uuid4().hex
    MEDIA_STATE[key] = {
        "url": url,
        "platform": platform,
        "comments": None,
        "created_at": time.time(),
    }
    return key


def _format_comment_chunks(comments: list[dict]) -> list[str]:
    """Форматирует комментарии в HTML-сообщения, не превышающие лимит Telegram."""
    chunks: list[str] = []
    current = "💬 <b>Топ комментариев</b>"

    for index, comment in enumerate(comments, 1):
        author = html.escape(str(comment.get("author") or "аноним"), quote=False)
        text = " ".join(str(comment.get("text") or "").split())
        if len(text) > 300:
            text = text[:297].rstrip() + "…"
        text = html.escape(text, quote=False)

        block = f"{index}. <b>{author}</b>"
        like_count = comment.get("like_count") or 0
        if like_count:
            block += f" · ❤️ {like_count}"
        block += f"\n{text}"

        if len(current) + len(block) + 2 > 4000:
            chunks.append(current)
            current = block
        else:
            current += "\n\n" + block

    chunks.append(current)
    return chunks


def _drop_expired_media_state() -> None:
    """Снимает состояние кнопок, чтобы словарь не рос бесконечно."""
    now = time.time()
    for key, state in list(MEDIA_STATE.items()):
        if now - state.get("created_at", now) > MEDIA_STATE_TTL:
            MEDIA_STATE.pop(key, None)


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
    MEDIA_STATE.clear()
    cleaner.sweep(force=True)


async def cleanup_downloads_loop(interval: int = config.CLEANUP_INTERVAL) -> None:
    """Периодическая уборка: снимает просроченные подтверждения и «хвосты»."""
    while True:
        await asyncio.sleep(interval)
        try:
            _drop_expired_uploads()
            _drop_expired_media_state()
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
        PENDING_DOWNLOADS[key] = {"url": text, "platform": platform, "meta": meta}

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
                    "platform": platform,
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

            media_key = _register_media_state(url, platform)
            await message.answer_video(
                video=FSInputFile(file_path),
                width=meta.get('width'),
                height=meta.get('height'),
                duration=meta.get('duration'),
                reply_markup=_media_kb(url, media_key, platform, meta.get('like_count')),
            )
            success = True

        # 2. ЕСЛИ НЕ ВИДЕО (например, картинка/карусель) и это Инстаграм — скачиваем фото
        elif platform == "instagram":
            photos, like_count = await dn.download_instagram_photos(url)

            if photos:
                photo_dir = Path(photos[0]).parent

                if len(photos) == 1:
                    media_key = _register_media_state(url, platform)
                    await message.answer_photo(
                        photo=FSInputFile(photos[0]),
                        reply_markup=_media_kb(url, media_key, platform, like_count),
                    )
                else:
                    # Если это карусель из нескольких фото
                    media_group = [
                        InputMediaPhoto(media=FSInputFile(p))
                        for p in photos[:10]  # Ограничение Telegram — до 10 медиа
                    ]
                    await message.answer_media_group(media=media_group)
                    # У альбома не может быть inline-клавиатуры, поэтому кнопки
                    # отправляем отдельным сообщением
                    media_key = _register_media_state(url, platform)
                    await message.answer(
                        "🔗",
                        reply_markup=_media_kb(url, media_key, platform, like_count),
                    )

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
        pending.get("meta") or {},
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
    platform = pending.get("platform") or dn._detect_platform(url)

    try:
        media_key = _register_media_state(url, platform)
        await bot.send_video(
            chat_id=pending["chat_id"],
            video=FSInputFile(file_path),
            width=meta.get('width'),
            height=meta.get('height'),
            duration=meta.get('duration'),
            reply_markup=_media_kb(url, media_key, platform, meta.get('like_count')),
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


@router.callback_query(F.data.startswith("likes:"))
async def cb_likes(callback: CallbackQuery) -> None:
    """Показывает точное количество лайков из источника (кнопка-счётчик)."""
    _, platform, raw_count = callback.data.split(":", 2)

    if not raw_count.isdigit():
        await callback.answer("Источник не сообщил количество лайков.", show_alert=True)
        return

    count = int(raw_count)
    name = PLATFORM_NAMES.get(platform, "источник")
    text = f"❤️ {count:,} лайков · {name}".replace(",", " ")
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("cmt:"))
async def cb_comments(callback: CallbackQuery) -> None:
    """Показывает топ комментариев к оригинальному видео."""
    key = callback.data.split(":", 1)[1]
    state = MEDIA_STATE.get(key)

    if not state:
        await callback.answer("Кнопка устарела, отправьте ссылку заново.", show_alert=True)
        return

    if not dn.supports_comments(state["platform"]):
        await callback.answer(
            "Комментарии для этой платформы недоступны.", show_alert=True
        )
        return

    cached = state.get("comments")
    if cached is not None:
        await callback.answer()
        comments = cached
    else:
        await callback.answer("Загружаю комментарии...")
        comments = await dn.get_top_comments(
            state["url"],
            state["platform"],
            limit=COMMENTS_LIMIT,
        )
        # Пустой результат не кешируем: пользователь может попробовать ещё раз.
        if comments:
            state["comments"] = comments

    if not comments:
        await callback.message.answer(
            "💬 Комментарии не найдены или недоступны для этой платформы."
        )
        return

    for chunk in _format_comment_chunks(comments):
        await callback.message.answer(chunk)

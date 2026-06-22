from pathlib import Path
from aiogram import F, Bot
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, InputMediaPhoto
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
    caption_text = text[:45] + "..." if len(text) > 45 else text

    # Флаг успешности отправки
    success = False
    file_path = None

    try:
        # 1. Сначала быстро запрашиваем метаданные, чтобы видео не косило на iPhone
        meta = await dn.get_video_meta(text, platform)
        width = meta.get('width')
        height = meta.get('height')
        duration = meta.get('duration')
        print(f" Метаданные видео: {width}x{height}, длительность: {duration}с")

        # 2. Скачиваем видеоролик вашим оригинальным методом
        file_path, downloaded_platform = await dn.download_video(text)

        if file_path and Path(file_path).exists():
            file_size_mb = Path(file_path).stat().st_size / (1024 * 1024)
            print(f" Файл скачан: {file_path}, размер: {file_size_mb:.2f} MB")
            
            try:
                print(" Отправка методом VIDEO напрямую в плеер...")
                await message.answer_video(
                    video=FSInputFile(file_path),
                    caption=caption_text,
                    width=width,        # Передаем реальную ширину
                    height=height,      # Передаем реальную высоту
                    duration=duration   # Передаем длительность
                )
                success = True
            except Exception as send_err:
                print(f"❌ Ошибка отправки в Telegram: {send_err}")
                
                try:
                    print(" Резервная попытка отправки документом...")
                    await message.answer_document(document=FSInputFile(file_path), caption=caption_text)
                    success = True
                except Exception as backup_err:
                    print(f"❌ Резервная отправка тоже не удалась: {backup_err}")

    except Exception as e:
        print(f"❌ Ошибка при скачивании видео: {e}")

        # 2. Если видео нет и это инстаграм — пробуем картинки
        if "There is no video" in str(e) and platform == "instagram":
            try:
                photo_paths = await dn.download_instagram_photos(text)
                if photo_paths:
                    media_group = []
                    for i, p_path in enumerate(photo_paths[:10]):
                        if i == 0:
                            media_group.append(InputMediaPhoto(media=FSInputFile(p_path), caption=caption_text))
                        else:
                            media_group.append(InputMediaPhoto(media=FSInputFile(p_path)))
                    
                    await message.answer_media_group(media=media_group)
                    success = True
                    
                    # Зачистка папки с картинками
                    parent_dir = Path(photo_paths[0]).parent
                    for p_path in photo_paths:
                        Path(p_path).unlink(missing_ok=True)
                    if parent_dir.exists():
                        parent_dir.rmdir()
            except Exception as img_err:
                print(f"❌ Ошибка обработки картинок Инстаграм: {img_err}")

    # Обязательно удаляем файл видео, если он остался на диске
    if file_path and Path(file_path).exists():
        Path(file_path).unlink(missing_ok=True)

    # 3. Если ничего не сработало — шлем крестик
    if not success:
        await message.answer("❌")
    
    # 4. В самом конце удаляем часики "⏳"
    try:
        await status_msg.delete()
    except Exception as delete_err:
        print(f"⚠️ Не удалось удалить статус-сообщение: {delete_err}")
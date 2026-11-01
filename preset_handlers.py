"""Роутер ИИ-пресетов: отдельные команды с жёстко заданным system-prompt.

Пресеты не используют историю диалога ``/ai`` — каждый запрос независим.
Выбор модели и сеть остаются в ``openai_service.py``.
"""

import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from openai_service import OpenRouterError, ask_openrouter
from presets import PRESETS, Preset

preset_router = Router()

# Лимит Telegram — 4096 символов; оставляем небольшой запас.
TELEGRAM_MESSAGE_LIMIT = 4000


def _split_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Режет длинный ответ по абзацам, чтобы уложиться в лимит Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n"):
        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)

        # Абзац сам длиннее лимита — режем принудительно.
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph

    if current:
        chunks.append(current)

    return chunks


def _build_messages(preset: Preset, text: str) -> list[dict]:
    """Собирает messages для OpenRouter: system-пресет + текст пользователя."""
    return [
        {"role": "system", "content": preset.system_prompt},
        {"role": "user", "content": text},
    ]


async def _run_preset(message: Message, preset: Preset, text: str) -> None:
    """Отправляет текст в модель с промптом пресета и возвращает ответ."""
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        answer = await asyncio.to_thread(ask_openrouter, _build_messages(preset, text))
    except OpenRouterError as e:
        answer = str(e)

    if not answer or not answer.strip():
        answer = "❌ Модель вернула пустой ответ. Попробуйте ещё раз."

    for chunk in _split_for_telegram(answer):
        # Ответ модели — произвольный текст: HTML-разметку не парсим,
        # иначе символы <, >, & сломают отправку.
        await message.answer(chunk, parse_mode=None)


@preset_router.message(Command("idioma"))
async def idioma_handler(message: Message) -> None:
    """Перевод: испанский → русский, любой другой → испанский."""
    preset = PRESETS["idioma"]
    args = (message.text or "").split(maxsplit=1)

    if len(args) < 2 or not args[1].strip():
        await message.answer(
            f"🌐 <b>{preset.description}</b>\n\n"
            "Напиши текст после команды:\n"
            "<code>/idioma hola, ¿cómo estás?</code>"
        )
        return

    await _run_preset(message, preset, args[1].strip())

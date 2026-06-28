import asyncio
import time
from datetime import datetime, timedelta, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from google import genai
from google.genai import types

from config import GEMINI_API_KEY

ai_router = Router()
client = genai.Client(api_key=GEMINI_API_KEY)

# Хранилище истории в памяти
user_history = {}
CONTEXT_TTL = 3600  # 1 час в секундах

async def load_history_from_telegram(message: Message):
    """
    Загружает последние сообщения из чата Telegram, 
    фильтрует их за последний час и формирует историю для Gemini.
    """
    user_id = message.from_user.id
    chat_id = message.chat.id
    bot_id = message.bot.id
    
    current_time = time.time()
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    
    loaded_messages = []
    
    try:
        # Запрашиваем последние 30 сообщений из этого чата
        async for msg in message.bot.get_chat_history(chat_id=chat_id, limit=30):
            # Если сообщение старее одного часа — останавливаем сбор
            if msg.date < hour_ago:
                break
                
            # Пропускаем саму команду /ai, которая вызвала этот процесс прямо сейчас, 
            # чтобы она не продублировалась в контексте
            if msg.message_id == message.message_id:
                continue
                
            role = None
            text = None
            
            # 1. Если сообщение от нашего бота — это ответ ИИ (model)
            if msg.from_user and msg.from_user.id == bot_id:
                role = "model"
                text = msg.text or msg.caption
                
            # 2. Если сообщение от пользователя и это был запрос к ИИ
            elif msg.from_user and msg.from_user.id == user_id:
                if msg.text and msg.text.startswith("/ai"):
                    role = "user"
                    # Убираем команду "/ai " из текста, оставляя только чистый вопрос
                    text = msg.text.split(maxsplit=1)[1] if len(msg.text.split(maxsplit=1)) > 1 else ""

            # Если текст и роль успешно определены, добавляем в список
            if role and text:
                loaded_messages.append({
                    "role": role,
                    "text": text,
                    "timestamp": msg.date.timestamp()
                })
                
    except Exception as e:
        print(f"⚠️ Не удалось загрузить историю из Telegram: {e}")
        return

    # Так как get_chat_history возвращает сообщения от новых к старым,
    # переворачиваем список, чтобы хронология была правильной (от старых к новым)
    loaded_messages.reverse()
    
    # Сохраняем восстановленную историю в глобальный словарь
    user_history[user_id] = loaded_messages


def clean_old_history(user_id: int):
    """Удаляет сообщения старше 1 часа"""
    if user_id not in user_history:
        return
    current_time = time.time()
    user_history[user_id] = [
        msg for msg in user_history[user_id] 
        if current_time - msg["timestamp"] < CONTEXT_TTL
    ]
    if not user_history[user_id]:
        del user_history[user_id]


def ask_gemini_with_context(user_id: int, new_prompt: str) -> str:
    try:
        # Очищаем то, что уже устарело внутри памяти
        clean_old_history(user_id)
        
        contents_payload = []
        
        # Если в памяти есть история (из оперативной памяти или только что скачанная)
        if user_id in user_history:
            for msg in user_history[user_id]:
                contents_payload.append(
                    types.Content(
                        role=msg["role"],
                        parts=[types.Part.from_text(text=msg["text"])]
                    )
                )
        
        # Добавляем текущий вопрос
        contents_payload.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=new_prompt)]
            )
        )
        
        # Запрос к Gemini
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents_payload,
        )
        
        ai_answer = response.text
        
        # Сохраняем новую реплику и ответ в память для следующих вопросов
        if user_id not in user_history:
            user_history[user_id] = []
            
        current_time = time.time()
        user_history[user_id].append({"role": "user", "text": new_prompt, "timestamp": current_time})
        user_history[user_id].append({"role": "model", "text": ai_answer, "timestamp": current_time})
        
        return ai_answer

    except Exception as e:
        return f"❌ Ошибка при обращении к Gemini: {e}"


@ai_router.message(Command("ai"))
async def ai_handler(message: Message):
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer("🤖 Пожалуйста, напишите вопрос после команды.\nПример: `/ai почему трава зеленая?`", parse_mode="Markdown")
        return

    question = args[1]
    user_id = message.from_user.id
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # ТРИГГЕР СКАЧИВАНИЯ ИСТОРИИ:
    # Если бота только что запустили (или прошел час тишины) и памяти по юзеру нет,
    # мы скачиваем сообщения за последний час напрямую из Telegram
    if user_id not in user_history:
        await load_history_from_telegram(message)
    
    # Отправляем запрос в Gemini
    answer = await asyncio.to_thread(ask_gemini_with_context, user_id, question)
    
    await message.answer(answer)
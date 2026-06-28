import asyncio
import time
from datetime import datetime, timedelta, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

# Импортируем сервис
from openai_service import ask_openrouter

ai_router = Router()

# Хранилище истории в памяти
user_history = {}
CONTEXT_TTL = 3600  # 1 час

async def load_history_from_telegram(message: Message):
    """Загружает последние сообщения из чата"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    bot_id = message.bot.id
    
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    loaded_messages = []
    
    try:
        async for msg in message.bot.get_chat_history(chat_id=chat_id, limit=30):
            if msg.date < hour_ago:
                break
            if msg.message_id == message.message_id:
                continue
                
            role = None
            text = None
            
            if msg.from_user and msg.from_user.id == bot_id:
                role = "assistant"
                text = msg.text or msg.caption
            elif msg.from_user and msg.from_user.id == user_id:
                if msg.text and msg.text.startswith("/ai"):
                    role = "user"
                    text = msg.text.split(maxsplit=1)[1] if len(msg.text.split(maxsplit=1)) > 1 else ""

            if role and text:
                loaded_messages.append({
                    "role": role,
                    "content": text,
                    "timestamp": msg.date.timestamp()
                })
    except Exception as e:
        print(f"⚠️ Не удалось загрузить историю: {e}")

    loaded_messages.reverse()
    user_history[user_id] = loaded_messages


def clean_old_history(user_id: int):
    """Удаляет старые сообщения"""
    if user_id not in user_history:
        return
    current_time = time.time()
    user_history[user_id] = [
        msg for msg in user_history[user_id] 
        if current_time - msg["timestamp"] < CONTEXT_TTL
    ]
    if not user_history[user_id]:
        del user_history[user_id]


def ask_ai_with_context(user_id: int, new_prompt: str) -> str:
    try:
        clean_old_history(user_id)
        
        contents_payload = []
        
        if user_id in user_history:
            for msg in user_history[user_id]:
                contents_payload.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        contents_payload.append({
            "role": "user",
            "content": new_prompt
        })
        
        # Вызов сервиса
        ai_answer = ask_openrouter(contents_payload)
        
        # Сохраняем в историю
        if user_id not in user_history:
            user_history[user_id] = []
            
        current_time = time.time()
        user_history[user_id].append({"role": "user", "content": new_prompt, "timestamp": current_time})
        user_history[user_id].append({"role": "assistant", "content": ai_answer, "timestamp": current_time})
        
        return ai_answer

    except Exception as e:
        return f"❌ Неожиданная ошибка: {e}"


@ai_router.message(Command("ai"))
async def ai_handler(message: Message):
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer("🤖 Пожалуйста, напишите вопрос после команды.\nПример: `/ai почему трава зеленая?`", parse_mode="Markdown")
        return

    question = args[1]
    user_id = message.from_user.id
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    if user_id not in user_history:
        await load_history_from_telegram(message)
    
    answer = await asyncio.to_thread(ask_ai_with_context, user_id, question)
    
    await message.answer(answer)
from openai import OpenAI
from config import OPEN_AI_API_KEY   # ← исправлено

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPEN_AI_API_KEY,
)

def ask_openrouter(messages: list, model: str = None) -> str:
    """
    Простая функция для запроса к OpenRouter.
    """
    if model is None:
        model = 'openrouter/free'
        #model = 'google/gemma-4-31b-it:free'

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content

    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str:
            return "❌ Временный rate limit на модели. Попробуйте чуть позже."
        elif "404" in error_str or "no endpoints" in error_str:
            return f"❌ Модель {model} сейчас недоступна. Попробуйте позже или смените модель."
        return f"❌ Ошибка при обращении к ИИ: {e}"
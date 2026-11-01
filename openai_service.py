from openai import OpenAI

from config import OPEN_AI_API_KEY

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPEN_AI_API_KEY,
)


class OpenRouterError(RuntimeError):
    """Ошибка запроса к OpenRouter: сеть, лимит или недоступная модель."""


def ask_openrouter(messages: list, model: str | None = None) -> str:
    """
    Простая функция для запроса к OpenRouter.

    При ошибке поднимает OpenRouterError, чтобы вызывающий код мог показать
    сообщение пользователю и не сохранять ошибку как ответ модели.
    """
    if model is None:
        model = "openrouter/free"
        # model = 'google/gemma-4-31b-it:free'
        # model = 'cognitivecomputations/dolphin-mistral-24b-venice-edition:free'

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = response.choices[0].message.content
        if not content:
            raise OpenRouterError("❌ Модель вернула пустой ответ. Попробуйте ещё раз.")
        return content

    except OpenRouterError:
        raise
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str:
            raise OpenRouterError(
                "❌ Временный rate limit на модели. Попробуйте чуть позже."
            ) from e
        if "404" in error_str or "no endpoints" in error_str:
            raise OpenRouterError(
                f"❌ Модель {model} сейчас недоступна. Попробуйте позже или смените модель."
            ) from e
        raise OpenRouterError(f"❌ Ошибка при обращении к ИИ: {e}") from e

from google import genai
from config import settings

client = genai.Client(api_key=settings.GEMINI_API_KEY)

def ask_gemini(prompt: str) -> str:
    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite', #'gemini-3.1-flash-lite', #'gemini-2.5-flash',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"❌ Ошибка при обращении к Gemini: {e}"
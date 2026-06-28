# Используем стабильную версию Python
FROM python:3.11-slim

# Устанавливаем ffmpeg, который необходим yt_dlp для склейки и обработки видео
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install aiogram yt-dlp google-genai

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код приложения в контейнер
COPY . .

# Команда для запуска бота при старте контейнера
CMD ["python", "main.py"]
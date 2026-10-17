# AGENTS.md — инструкции для AI-ассистентов

## О проекте

Telegram-бот для скачивания видео/фото с YouTube, TikTok, Instagram, Facebook + встроенный AI-ассистент (`/ai`).

- Стек: Python 3.11, aiogram 3.x, yt-dlp, instaloader, OpenRouter
- Запуск: `python main.py` или `docker compose up --build`
- Конфигурация: через `.env` (см. `config.py`)

## ⚠️ Правило обновления схемы

**При изменении структуры проекта (новые модули, импорты, зависимости) — обновляй `project_structure.drawio`.**

Схема в формате draw.io лежит в корне и содержит карту модулей и связей между ними. Если она разошлась с кодом — её надо поправить, а не оставлять устаревшей.

Как править: файл — обычный XML. Ищи ячейки по `id` (`main`, `loader`, `handlers`, `aih`, `downloader`, `openai`, `config` и т.д.). Стрелки — ячейки с `edge="1"`, у каждой есть `source` и `target`. Добавляй новые блоки рядом с существующими и связывай через `mxCell` с `edge="1"`.

## Архитектура (кратко)

```
main.py            → точка входа, подключает роутеры, polling
loader.py          → создаёт bot, dp, dn (Downloader), cleaner (DownloadsCleaner)
handlers.py        → router загрузок: /start, /init_tg, ловля ссылок
ai_handlers.py     → ai_router: /ai, история в памяти (TTL 1 час)
downloader.py      → class Downloader: yt-dlp + instaloader
cleanup.py         → class DownloadsCleaner: удаление временных файлов
openai_service.py  → ask_openrouter() через OpenRouter
config.py          → чтение .env, константы
```

Поток загрузки: ссылка → `_detect_platform()` → `get_video_meta()` (проверка длительности и размера) → при размере больше `CONFIRM_SIZE_MB` — кнопка подтверждения → `_run_download()` → страховочная проверка фактического размера файла (тоже с кнопкой, если больше лимита) → отправка → удаление временного файла через `cleaner`.

## Известные проблемы

- **`gemini_service.py` сломан**: в нём `from config import settings`, но в `config.py` нет объекта `settings` — только отдельные константы. При импорте будет `AttributeError`. Модуль сейчас нигде не подключён. Если понадобится — исправить импорт на `from config import GEMINI_API_KEY`.

## Особенности, о которых легко забыть

- **`cookies.txt`** — формат Netscape (табулированный). Путь захардкожен как `/app/cookies.txt` в `downloader.py` и `handlers.py` (Docker). Перезаписывается командой `/init_tg <sessionid>` только для ADMIN_ID.
- **Лимиты Telegram**: видео длиннее 600 секунд бот пропускает; размер больше `CONFIRM_SIZE_MB` (по умолчанию 100 МБ) требует подтверждения кнопкой — сначала до скачивания (по оценке), затем перед отправкой (по факту; перекодировка в x264 может раздуть файл).
- **`PENDING_DOWNLOADS`** — словарь в памяти. После рестарта бота все ожидающие подтверждения теряются.
- **`PENDING_UPLOADS`** — уже скачанные крупные файлы, ждущие кнопки «Отправить/Удалить». Тоже in-memory; файл удаляется по кнопке («Отправить» или «Удалить») либо фоновой задачей `cleanup_downloads_loop()` через `config.UPLOAD_TTL` (30 мин).
- **Временные файлы** в `downloads/` удаляются через `DownloadsCleaner` (`cleanup.py`): `remove(path)` — конкретный файл (с «хвостами» `.part`/`.fXXX`) или папка целиком; `sweep(protected, force)` — периодический проход по каталогу (не трогает файлы из `protected`; `force=True` сносит всё, вызывается в `purge_downloads()` при старте). У `sweep()` есть неблокирующий замок: параллельный вызов просто выходит.
- **Куки инстаграм** нужны для `yt-dlp` (Instagram блокирует без sessionid) и для `instaloader`.
- Локальный Bot API сервер (`telegram-bot-api` в docker-compose) используется, если задан `TELEGRAM_API_URL`; иначе — официальный сервер Telegram.

## Безопасность

- `.env` содержит секреты (`BOT_TOKEN`, `GEMINI_API_KEY`, `OPEN_AI_API_KEY`). Чтение запрещено через `opencode.json` (permission deny) — не пытайся читать и не выводи содержимое.
- `ADMIN_ID` — единственный пользователь, которому доступна команда `/init_tg`.
- `cookies.txt` содержит sessionid — не коммитить в публичные репозитории.

## Стиль кода

Стиль задаётся автоматикой, а не вручную:

- **`pyproject.toml`** — конфиг `ruff`: линтер + форматтер (замена flake8/black/isort). Проверка: `ruff check .`, формат: `ruff format .`
- **`.editorconfig`** — отступы и переносы строк для редакторов
- **`.gitattributes`** — нормализация переносов строк в git

Ручные правила (то, что автоматика не поймёт):

- **Переносы строк — только LF.** Так задано в `.editorconfig` (`end_of_line = lf`) и закреплено в `.gitattributes` (`* text=auto eol=lf`). Настраивай редактор/IDE на LF; если файл пришёл в CRLF — приведи его к LF перед коммитом, иначе весь файл уйдёт в дифф.
- **Проектируем по SOLID, в первую очередь Single Responsibility.** Одна сущность — одна причина меняться. Каждую операцию (удаление, скачивание, чтение конфига) держим в одном месте и переиспользуем, а не дублируем.
  - Пример: удаление временных файлов живёт только в `cleanup.py` (`DownloadsCleaner`) — `handlers.py` и `downloader.py` вызывают `cleaner.remove()` / `cleaner.sweep()`, а не работают с файлами напрямую.
  - Зависимости передаём снаружи (как `Downloader(cleaner)` в `loader.py`), а не создаём внутри класса.
- Комментарии и сообщения пользователю — на русском.
- aiogram 3.x: роутеры (`Router`), фильтры `F`, хендлеры с типизированными аннотациями.
- Синхронные операции (yt-dlp, instaloader, OpenRouter) оборачиваются в `asyncio.to_thread` — не блокировать event loop.
- Не вводи зависимости без необходимости: всё, что нужно в рантайме, — в `requirements.txt`.
- После правок кода — проверяй, что схема `project_structure.drawio` соответствует изменениям.

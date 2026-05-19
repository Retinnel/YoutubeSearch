# YouTube Shorts Finder

FastAPI-сервис для поиска YouTube Shorts по тематике, их транскрибации и оценки релевантности через n8n + LLM.

## Архитектура

```
Telegram → n8n → FastAPI → yt-dlp → YouTube
                         ↓
                   Транскрипция (youtube-transcript-api / yt-dlp fallback)
                         ↓
               n8n (GPT оценка релевантности) → Telegram
```

**YouTube API не используется** — весь поиск и транскрипция через `yt-dlp`.

## Быстрый старт

### 1. Зависимости

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Настройка

```bash
cp .env.example .env
```

Отредактируй `.env`:

```env
API_KEY=замени_на_надёжный_секрет
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000
```

### 3. Запуск

```bash
start.bat
# или
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Документация API: http://localhost:8000/docs

## API эндпоинты

Все запросы требуют заголовок `X-API-KEY`.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | Статус сервиса |
| POST | `/api/v1/search_shorts` | Поиск Shorts по запросу |
| POST | `/api/v1/get_transcript` | Транскрипция видео |
| POST | `/api/v1/process_channel` | Shorts + транскрипты с канала |

### Пример: поиск Shorts

```bash
curl -X POST http://localhost:8000/api/v1/search_shorts \
  -H "X-API-KEY: your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "python tutorial", "max_results": 10}'
```

### Пример: транскрипция

```bash
curl -X POST http://localhost:8000/api/v1/get_transcript \
  -H "X-API-KEY: your_key" \
  -H "Content-Type: application/json" \
  -d '{"video_id": "dQw4w9WgXcQ"}'
```

## n8n воркфлоу

Импортируй `workflow_v2.json` в n8n.

**Важно:** n8n запускается в Docker — для доступа к FastAPI на хосте используется адрес `host.docker.internal:8000`.

После импорта замени `change_me_to_a_strong_secret` на свой ключ в нодах:
- Search Shorts (FastAPI)
- Get Transcript (FastAPI)

### Цепочка нод

```
Telegram Trigger
  → Expand Queries (GPT: расширяет тему в поисковые запросы)
  → Parse Queries (Code: парсит JSON ответ GPT)
  → Search Shorts (FastAPI: yt-dlp поиск)
  → Enrich Shorts (Code: добавляет тему к каждому шорту)
  → Split Shorts (Split Out: разбивает массив)
  → Get Transcript (FastAPI: транскрипция)
  → Merge Transcript (Code: объединяет шорт + транскрипт)
  → Score Relevance (GPT: оценка 1–10)
  → Parse Score (Code: парсит оценку)
  → Filter Score >= 7
  → Aggregate Results
  → Format Telegram Message
  → Send to Telegram
```

### Запуск n8n через Docker + ngrok

```bash
docker run -it --rm --name n8n \
  -p 5678:5678 \
  -e N8N_EDITOR_BASE_URL=https://your-subdomain.ngrok-free.app \
  -e WEBHOOK_URL=https://your-subdomain.ngrok-free.app \
  -e N8N_EXECUTE_COMMAND_ENABLED=true \
  -v n8n_data:/home/node/.n8n \
  docker.n8n.io/n8nio/n8n
```

> ⚠️ `N8N_EDITOR_BASE_URL` и `WEBHOOK_URL` должны включать `https://`, иначе GPT-нода падает с ошибкой `Invalid URL`.

## Транскрипция

Стратегия с fallback:
1. **youtube-transcript-api** — быстро, без скачивания
2. **yt-dlp** — скачивает авто-субтитры (VTT), если первый способ недоступен

Если оба не сработали — шорт оценивается GPT только по заголовку и названию канала.

## Логи

Логи пишутся в `logs/app.log` (ротация 10 МБ, хранение 7 дней) и в консоль с цветовой подсветкой.

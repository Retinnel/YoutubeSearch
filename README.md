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

### Вариант A: Docker Compose (рекомендуется)

Поднимает n8n + FastAPI + ngrok одной командой.

**1. Заполни `.env`:**

```env
API_KEY=замени_на_надёжный_секрет
NGROK_DOMAIN=unmaledictory-nasally-tanika.ngrok-free.dev
NGROK_AUTHTOKEN=твой_токен_из_ngrok_dashboard
```

`NGROK_AUTHTOKEN` берётся на [dashboard.ngrok.com](https://dashboard.ngrok.com) → Your Authtoken.

**2. Запусти:**

```bash
start_compose.bat
# или
docker compose up --build -d
```

**3. Импортируй `workflow_v3.json` в n8n** — URL уже настроен на `http://youtube-api:8000`.

Логи: `docker compose logs -f`  
Остановить: `docker compose down`

---

### Вариант B: FastAPI локально (без Docker)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Настройка

```bash
cp .env.example .env
```

Отредактируй `.env` (нужны только `API_KEY` и порт, ngrok-поля не нужны):

```env
API_KEY=замени_на_надёжный_секрет
LOG_LEVEL=INFO
```

### 3. Запуск FastAPI

```bash
start.bat
# или
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

n8n запускай через `runn8n.bat`, ngrok — через `ngrok_start.bat`.

> ⚠️ В этом варианте в нодах workflow нужен URL `http://host.docker.internal:8000` вместо `http://youtube-api:8000`.

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

Импортируй `workflow_v3.json` в n8n.

После импорта замени `change_me_to_a_strong_secret` на свой ключ в нодах:
- Search Shorts (FastAPI)
- Get Transcript (FastAPI)

| Способ запуска | URL в нодах workflow |
|---|---|
| Docker Compose (`start_compose.bat`) | `http://youtube-api:8000` ✅ уже в v3 |
| Локально (`runn8n.bat` + `start.bat`) | `http://host.docker.internal:8000` |

### Цепочка нод

```
Telegram Trigger
  → Expand Queries (GPT: расширяет тему в поисковые запросы)
  → Parse Queries (Code: парсит JSON ответ GPT)
  → Search Shorts (FastAPI: yt-dlp поиск + фильтр по просмотрам)
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

### Запуск через Docker Compose (рекомендуется)

```bash
start_compose.bat
```

### Запуск n8n вручную (старый способ)

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

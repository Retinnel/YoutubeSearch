# YouTube Shorts Finder

FastAPI-сервис для поиска YouTube Shorts по тематике, транскрибации и оценки релевантности через n8n + GPT.

**YouTube Data API не используется** — весь поиск и транскрипция через `yt-dlp`.

## Архитектура

```
Telegram → n8n → FastAPI → yt-dlp → YouTube
                         ↓
                   Транскрипция (youtube-transcript-api / yt-dlp fallback)
                         ↓
               n8n (GPT оценка 1–10) → Telegram
```

---

## Быстрый старт

### Вариант A: Docker Compose (рекомендуется)

Поднимает **n8n + FastAPI + ngrok** одной командой. Все данные n8n сохраняются в volume `n8n_data` — твои воркфлоу и credentials не пропадут.

**1. Заполни `.env`:**

```env
API_KEY=замени_на_надёжный_секрет
NGROK_DOMAIN=your-subdomain.ngrok-free.app
NGROK_AUTHTOKEN=твой_токен_из_ngrok_dashboard
```

`NGROK_AUTHTOKEN` — на [dashboard.ngrok.com](https://dashboard.ngrok.com) → **Your Authtoken**.

**2. Запусти:**

```bat
start_compose.bat
```

или вручную:

```bash
docker compose up --build -d
```

**3. Открой n8n:** http://localhost:5678 (или через ngrok-домен)

**4. Импортируй `workflow_v3.json`** — URL уже настроен на `http://youtube-api:8000`.

**Полезные команды:**

```bash
docker compose logs -f          # логи всех сервисов
docker compose logs youtube-api # логи только FastAPI
docker compose down             # остановить всё
docker compose up -d            # запустить без пересборки
docker compose up --build -d    # запустить с пересборкой FastAPI
```

---

### Вариант B: FastAPI локально + n8n в Docker (ручной способ)

Используй если не хочешь Docker Compose.

**1. Установи зависимости:**

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**2. Настрой `.env`:**

```env
API_KEY=замени_на_надёжный_секрет
LOG_LEVEL=INFO
```

**3. Запусти:**

```bat
start.bat          # FastAPI на порту 8000
runn8n.bat         # n8n в Docker
ngrok_start.bat    # ngrok туннель
```

> ⚠️ В нодах workflow замени URL на `http://host.docker.internal:8000` (n8n в Docker не видит `localhost` хоста).

> ⚠️ `N8N_EDITOR_BASE_URL` и `WEBHOOK_URL` в `runn8n.bat` должны включать `https://`, иначе GPT-нода падает с ошибкой `Invalid URL`.

Документация API: http://localhost:8000/docs

---

## n8n воркфлоу

Импортируй `workflow_v3.json`. После импорта замени `change_me_to_a_strong_secret` на свой `API_KEY` в нодах:
- **Search Shorts (FastAPI)**
- **Get Transcript (FastAPI)**

| Способ запуска | URL в нодах |
|---|---|
| Docker Compose | `http://youtube-api:8000` ✅ уже задан в v3 |
| Вариант B (локально) | `http://host.docker.internal:8000` |

### Цепочка нод

```
Telegram Trigger
  → Expand Queries        — GPT расширяет тему в список поисковых запросов
  → Parse Queries         — парсит JSON из GPT
  → Search Shorts         — yt-dlp поиск + фильтр по длине и просмотрам
  → Enrich Shorts         — добавляет тему к каждому шорту
  → Split Shorts          — разбивает массив на отдельные элементы
  → Get Transcript        — транскрипция через FastAPI
  → Merge Transcript      — объединяет шорт + транскрипт
  → Score Relevance       — GPT оценивает релевантность 1–10
  → Parse Score           — парсит оценку из JSON
  → Filter Score >= 7     — отсеивает нерелевантные
  → Aggregate Results     — собирает все поля
  → Format Telegram Message
  → Send to Telegram
```

---

## API эндпоинты

Все запросы требуют заголовок `X-API-KEY`.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/health` | Статус сервиса (без авторизации) |
| POST | `/api/v1/search_shorts` | Поиск Shorts по запросу |
| POST | `/api/v1/get_transcript` | Транскрипция видео |
| POST | `/api/v1/process_channel` | Shorts + транскрипты с канала |

**Поиск Shorts:**

```bash
curl -X POST http://localhost:8000/api/v1/search_shorts \
  -H "X-API-KEY: your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "python tutorial", "max_results": 20, "min_views": 10000}'
```

**Транскрипция:**

```bash
curl -X POST http://localhost:8000/api/v1/get_transcript \
  -H "X-API-KEY: your_key" \
  -H "Content-Type: application/json" \
  -d '{"video_id": "dQw4w9WgXcQ"}'
```

---

## Транскрипция

Стратегия с автоматическим fallback:
1. **youtube-transcript-api** — быстро, без скачивания видео
2. **yt-dlp** — скачивает авто-субтитры (VTT), если первый способ недоступен

Если оба не сработали — шорт всё равно оценивается GPT по заголовку и названию канала.

---

## Логи

- **Консоль** — цветной вывод
- **Файл** — `logs/app.log`, ротация 10 МБ, хранение 7 дней
- В Docker: `docker compose logs -f youtube-api`

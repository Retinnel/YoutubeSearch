@echo off
echo Starting YouTube Shorts Finder (Docker Compose)...
docker compose up --build -d
echo.
echo All services started:
echo   n8n:         http://localhost:5678
echo   FastAPI:     http://localhost:8000/docs  (only on host, not exposed externally)
echo   ngrok:       https://%NGROK_DOMAIN%
echo.
echo To stop: docker compose down
echo To view logs: docker compose logs -f

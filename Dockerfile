FROM python:3.11-slim

WORKDIR /app

# Install system deps:
# - ffmpeg: needed for subtitle extraction and Whisper audio
# - nodejs: needed for yt-dlp to solve YouTube's n-challenge (otherwise many formats are missing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

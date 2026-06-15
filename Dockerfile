FROM python:3.11-slim

WORKDIR /app

# Install system deps:
# - ffmpeg: needed for subtitle extraction and Whisper audio
# - nodejs: needed for yt-dlp to solve YouTube's n-challenge (otherwise many formats are missing)
# - libgomp1: required by CTranslate2 (faster-whisper's inference backend)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Whisper 'small' model so first-request doesn't timeout.
# The model (~244MB) is baked into the image layer.
RUN python -c "\
from faster_whisper import WhisperModel; \
print('Downloading Whisper small model...'); \
WhisperModel('small', device='cpu', compute_type='float32'); \
print('Whisper model cached.')"

COPY app/ ./app/

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

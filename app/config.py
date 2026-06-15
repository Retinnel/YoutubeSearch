from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: str = "change_me"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    # Path to a Netscape-format cookies.txt file exported from your browser.
    # Required to bypass YouTube bot-detection for yt-dlp and transcript API.
    # Export via browser extension, e.g. "Get cookies.txt LOCALLY" for Chrome.
    cookies_file: str = ""
    # Set to true to enable Whisper as 3rd transcription fallback.
    # Requires: pip install faster-whisper + ffmpeg in PATH. Much slower.
    whisper_enabled: bool = False
    # Groq API key for cloud Whisper transcription (whisper-large-v3).
    # Free tier: 7200 min/day. Much better quality than local models.
    # Get key at: https://console.groq.com/keys
    # If set, Groq is used instead of local faster-whisper when force_whisper=True.
    groq_api_key: str = ""
    # Local Whisper model size when Groq is not available.
    # Options: tiny, base, small, medium, large-v3 (larger = better quality, more RAM)
    whisper_model: str = "small"
    # OpenAI API key (optional) - set OPENAI_KEY in .env to enable OpenAI Whisper
    openai_key: str = ""
    # When true, prefer OpenAI Whisper (API) before caption/subtitle methods
    openai_prefer: bool = False
    # ngrok settings (used in docker-compose, ignored here)
    ngrok_domain: str = ""
    ngrok_authtoken: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

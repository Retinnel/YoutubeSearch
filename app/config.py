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
    # Requires: pip install openai-whisper + ffmpeg in PATH. Much slower.
    whisper_enabled: bool = False
    # ngrok settings (used in docker-compose, ignored here)
    ngrok_domain: str = ""
    ngrok_authtoken: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

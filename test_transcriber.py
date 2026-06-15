import os, asyncio, traceback, sys, json
from app.config import settings
print("settings.whisper_enabled=", settings.whisper_enabled)
print("settings.openai_key=", bool(getattr(settings, "openai_key", "")))
print("settings.openai_prefer=", getattr(settings, "openai_prefer", False))
print("cookies_file exists:", os.path.exists(settings.cookies_file) if getattr(settings,"cookies_file","") else None)
from app.services import transcriber as t
video_id = "6MRZ3khJUYo"
def run_sync(fn, *args):
    try:
        print("\n== Running", fn.__name__)
        res = fn(*args)
        print("Result:", res)
    except Exception as e:
        print("Exception in", fn.__name__)
        traceback.print_exc()
async def run_async(fn, *args):
    try:
        print("\n== Running async", fn.__name__)
        res = await fn(*args)
        print("Async Result:", res)
    except Exception as e:
        print("Exception in async", fn.__name__)
        traceback.print_exc()
# Try API captions
try:
    api_res = t._get_via_api(video_id, "en", settings.cookies_file)
    print("\n_api_res:", api_res)
except Exception as e:
    print("api exception")
    traceback.print_exc()
# Try yt-dlp
try:
    ytdlp_res = t._get_via_ytdlp(video_id, "en", settings.cookies_file)
    print("\n_ytdlp_res:", ytdlp_res)
except Exception as e:
    print("ytdlp exception")
    traceback.print_exc()
# Try OpenAI (full)
if getattr(settings, "openai_key", ""):
    try:
        openai_res = t._get_via_openai_full(video_id, "en", settings.cookies_file)
        print("\n_openai_res:", openai_res)
    except Exception as e:
        print("openai exception")
        traceback.print_exc()
# Try overall sync function
try:
    sync_res = t._get_transcript_sync(video_id, "en", use_whisper=settings.whisper_enabled, force_whisper=False, cookies_file=settings.cookies_file)
    print("\n_sync_res:", sync_res)
except Exception as e:
    print("sync exception")
    traceback.print_exc()

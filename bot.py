import os
import asyncio
import time
import re
import yt_dlp
from pyrogram import Client, filters
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
PORT = int(os.environ.get("PORT", 8080))

app = Client("m3u8_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["recorder_bot"]
cookies_col = db["cookies"]

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

async def start_health_check():
    server = await asyncio.start_server(lambda r, w: w.close(), '0.0.0.0', PORT)
    async with server: await server.serve_forever()

# --- PARSER FOR AIO SUPPORT ---
def parse_aio_args(args):
    """
    Parses: url [duration] [name] [-aio]
    Example: https://site.com/s.m3u8 00:01:00 my_show -aio
    """
    data = {"url": None, "duration": None, "name": None, "aio": False}
    if not args: return data
    
    # Check for -aio flag
    if "-aio" in args:
        data["aio"] = True
        args = [a for a in args if a != "-aio"]
    
    if len(args) >= 1: data["url"] = args[0]
    if len(args) >= 2: data["duration"] = args[1] # Format: HH:MM:SS
    if len(args) >= 3: data["name"] = args[2]
    
    return data

async def progress(current, total, message, start_time):
    now = time.time()
    diff = now - start_time
    if round(diff % 5.0) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / (diff if diff > 0 else 1)
        try:
            await message.edit(f"📤 **AIO Uploading...**\n`{percentage:.1f}%` | `{speed/1024/1024:.2f} MB/s`")
        except: pass

# --- DOWNLOAD ENGINE ---
async def download_engine(m, args, cookie_tag=None):
    parsed = parse_aio_args(args)
    if not parsed["url"]:
        return await m.reply_text("❌ URL missing.")
    
    status = await m.reply_text("🚀 **AIO Engine Initializing...**")
    cookie_file = f"temp_{m.id}.txt"
    
    # Custom Name Logic
    out_name = parsed["name"] if parsed["name"] else "%(title)s"
    
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': f'{DOWNLOAD_DIR}/{m.id}/{out_name}.%(ext)s',
        'merge_output_format': 'mp4',
        'quiet': True,
        'impersonate': 'chrome',
        'extractor_args': {'generic': {'impersonate': True}},
    }

    # If -aio flag or specific sites are used, use aggressive headers
    if parsed["aio"] or "playyonogames" in parsed["url"]:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': 'https://playyonogames.in/',
            'Origin': 'https://playyonogames.in/',
            'Accept-Language': 'en-US,en;q=0.9',
        }

    # Handle Duration (Recording specific length)
    if parsed["duration"]:
        # yt-dlp uses --download-sections
        ydl_opts['download_sections'] = f"*00:00:00-{parsed['duration']}"
        ydl_opts['force_keyframes_at_cuts'] = True

    if cookie_tag:
        data = await cookies_col.find_one({"tag": cookie_tag})
        if data:
            with open(cookie_file, "w") as f: f.write(data["content"])
            ydl_opts['cookiefile'] = cookie_file

    try:
        await status.edit("📥 **Downloading/Recording...**\n_Applying AIO Bypasses_")
        
        loop = asyncio.get_event_loop()
        def run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(parsed["url"], download=True)
                return ydl.prepare_filename(info)

        f_path = await loop.run_in_executor(None, run_ydl)

        # File validation
        if not os.path.exists(f_path):
            base = os.path.splitext(f_path)[0]
            for ext in ['.mp4', '.mkv', '.ts']:
                if os.path.exists(base + ext):
                    f_path = base + ext
                    break

        await status.edit("📤 **Recording Done. Uploading (2GB Max)...**")
        start_up = time.time()
        await app.send_document(
            chat_id=m.chat.id,
            document=f_path,
            caption=f"✅ **AIO Completed**\n📦 **Name:** `{os.path.basename(f_path)}`",
            progress=progress,
            progress_args=(status, start_up)
        )
        os.remove(f_path)
        await status.delete()

    except Exception as e:
        await status.edit(f"❌ **AIO Error:**\n`{str(e)[:500]}`")
    finally:
        if os.path.exists(cookie_file): os.remove(cookie_file)

# --- HANDLERS ---
@app.on_message(filters.command("rec"))
async def rec_handler(_, m):
    await download_engine(m, m.command[1:])

@app.on_message(filters.command("ddl"))
async def ddl_handler(_, m):
    # For DDL, the last arg is usually the cookie tag
    if len(m.command) < 3: return
    tag = m.command[-1]
    args = m.command[1:-1]
    await download_engine(m, args, tag)

@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    await m.reply_text("🤖 **AIO m3u8 Recorder Online**\nUsage:\n`/rec [URL] [Duration] [Name] -aio`")

async def main():
    await asyncio.gather(start_health_check(), app.start())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())

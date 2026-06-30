import os
import asyncio
import time
import re
from datetime import datetime
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
stats_col = db["stats"]

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- TCP HEALTH CHECK ---
async def health_check():
    server = await asyncio.start_server(lambda r, w: w.close(), '0.0.0.0', PORT)
    async with server:
        await server.serve_forever()

# --- HELPERS ---
async def update_stats():
    await stats_col.update_one({"id": "bot_stats"}, {"$inc": {"total_downloads": 1}}, upsert=True)

async def progress(current, total, message, start_time):
    now = time.time()
    diff = now - start_time
    if round(diff % 4.0) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        try:
            await message.edit(f"📤 **Uploading...**\n`{percentage:.1f}%` | `{speed/1024/1024:.1f} MB/s`")
        except: pass

# --- COMMANDS ---
@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    await m.reply_text("✅ **Bot Fixed & Active**\nIncludes Cloudflare Bypass & 2GB Support.")

@app.on_message(filters.command("addcookie"))
async def add_cookie(_, m):
    if not m.reply_to_message or not m.reply_to_message.document:
        return await m.reply_text("❌ Reply to a cookies .txt file.")
    tag = m.command[1] if len(m.command) > 1 else "default"
    path = await m.reply_to_message.download()
    with open(path, "r") as f: content = f.read()
    await cookies_col.update_one({"tag": tag}, {"$set": {"content": content}}, upsert=True)
    os.remove(path)
    await m.reply_text(f"✅ Cookie saved as `{tag}`")

@app.on_message(filters.command("stats"))
async def stats_cmd(_, m):
    data = await stats_col.find_one({"id": "bot_stats"})
    count = data.get("total_downloads", 0) if data else 0
    await m.reply_text(f"📊 **Total Videos Processed:** {count}")

async def download_engine(m, url, tag=None):
    status = await m.reply_text("⚙️ **Bypassing Protections...**")
    cookie_file = f"temp_{m.id}.txt"
    
    # ADVANCED OPTIONS TO BYPASS 403/CLOUDFLARE
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': f'{DOWNLOAD_DIR}/{m.id}_%(title)s.%(ext)s',
        'merge_output_format': 'mp4',
        'quiet': True,
        # Impersonate Chrome to bypass Cloudflare
        'impersonate': 'chrome', 
        'extractor_args': {'generic': {'impersonate': True}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Connection': 'keep-alive',
        }
    }

    if tag:
        data = await cookies_col.find_one({"tag": tag})
        if data:
            with open(cookie_file, "w") as f: f.write(data["content"])
            ydl_opts['cookiefile'] = cookie_file

    try:
        await status.edit("📥 **Downloading/Recording...**\n_This might take a minute for m3u8..._")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Handle the download
            info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            f_path = ydl.prepare_filename(info)
            # Fix path if merged
            if not os.path.exists(f_path):
                f_path = f_path.rsplit('.', 1)[0] + ".mp4"

        await status.edit("📤 **Uploading to Telegram...**")
        start_up = time.time()
        await app.send_document(
            chat_id=m.chat.id,
            document=f_path,
            caption=f"✅ **Title:** {info.get('title')}",
            progress=progress,
            progress_args=(status, start_up)
        )
        await update_stats()
        if os.path.exists(f_path): os.remove(f_path)
        await status.delete()

    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg:
            error_msg = "❌ **403 Forbidden**: Site blocked the bot. Try adding/updating Cookies."
        elif "m3u8" in error_msg:
            error_msg = "❌ **m3u8 Error**: Link is invalid or expired."
        await status.edit(f"❌ **Error:** `{error_msg}`")
    finally:
        if os.path.exists(cookie_file): os.remove(cookie_file)

@app.on_message(filters.command("rec"))
async def rec_handler(_, m):
    if len(m.command) < 2: return
    await download_engine(m, m.command[1])

@app.on_message(filters.command("ddl"))
async def ddl_handler(_, m):
    if len(m.command) < 3: return
    await download_engine(m, m.command[1], m.command[2])

# --- BOOT ---
async def main():
    await asyncio.gather(health_check(), app.start())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())

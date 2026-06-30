import os
import asyncio
import time
from datetime import datetime
import yt_dlp
from pyrogram import Client, filters, errors
from motor.motor_asyncio import AsyncIOMotorClient

# --- ENV VARS (Set these in Koyeb) ---
API_ID = int(os.environ.get("API_ID", 26826540))
API_HASH = os.environ.get("API_HASH", "32d454f51fc7b3b3c7d51c4f80f628b5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
PORT = int(os.environ.get("PORT", 8080))

# --- CLIENTS ---
app = Client("m3u8_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["recorder_bot"]
cookies_col = db["cookies"]
stats_col = db["stats"]

START_TIME = time.time()
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- TCP HEALTH CHECK ---
async def health_check():
    server = await asyncio.start_server(lambda r, w: w.close(), '0.0.0.0', PORT)
    async with server:
        await server.serve_forever()

# --- HELPERS ---
def get_readable_time(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{int(d)}d {int(h)}h {int(m)}m {int(s)}s"

async def update_stats():
    await stats_col.update_one({"id": "bot_stats"}, {"$inc": {"total_downloads": 1}}, upsert=True)

def is_cookie_expired(content):
    expired, total = 0, 0
    now = time.time()
    for line in content.splitlines():
        if line.startswith("#") or not line.strip(): continue
        parts = line.split("\t")
        if len(parts) >= 7:
            total += 1
            if int(parts[4]) < now: expired += 1
    return expired, total

# --- PROGRESS BAR ---
async def progress(current, total, message, start_time):
    now = time.time()
    diff = now - start_time
    if round(diff % 4.0) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        await message.edit(f"📤 **Uploading...**\n`{percentage:.1f}%` at `{speed/1024/1024:.1f} MB/s`")

# --- COMMANDS ---
@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    await m.reply_text("✨ **Bot Online!**\nUse /help to see all available commands.")

@app.on_message(filters.command("help"))
async def help_cmd(_, m):
    await m.reply_text(
        "**Available Commands:**\n"
        "• `/rec [URL]` - Standard Download/Record\n"
        "• `/ddl [URL] [Tag]` - Download using a cookie tag\n"
        "• `/addcookie [Tag]` - Reply to a cookie .txt file\n"
        "• `/chkcookie [Tag]` - Check if a cookie is expired\n"
        "• `/status` - Bot Uptime\n"
        "• `/stats` - Total downloads processed"
    )

@app.on_message(filters.command("status"))
async def status_cmd(_, m):
    await m.reply_text(f"⏳ **Uptime:** {get_readable_time(time.time() - START_TIME)}")

@app.on_message(filters.command("stats"))
async def stats_cmd(_, m):
    data = await stats_col.find_one({"id": "bot_stats"})
    count = data.get("total_downloads", 0) if data else 0
    await m.reply_text(f"📊 **Total Videos Processed:** {count}")

@app.on_message(filters.command("addcookie"))
async def add_cookie(_, m):
    if not m.reply_to_message or not m.reply_to_message.document:
        return await m.reply_text("❌ Reply to a cookies .txt file.")
    if len(m.command) < 2:
        return await m.reply_text("❌ Usage: `/addcookie [tag]`")
    
    tag = m.command[1]
    path = await m.reply_to_message.download()
    with open(path, "r") as f: content = f.read()
    await cookies_col.update_one({"tag": tag}, {"$set": {"content": content}}, upsert=True)
    os.remove(path)
    await m.reply_text(f"✅ Cookie saved as `{tag}`")

@app.on_message(filters.command("chkcookie"))
async def chk_cookie(_, m):
    if len(m.command) < 2: return await m.reply_text("❌ Usage: `/chkcookie [tag]`")
    data = await cookies_col.find_one({"tag": m.command[1]})
    if not data: return await m.reply_text("❌ Tag not found.")
    exp, total = is_cookie_expired(data["content"])
    await m.reply_text(f"🍪 **Tag:** {m.command[1]}\n**Expired:** {exp}/{total}\n**Status:** {'🔴 Expired' if exp==total else '🟢 OK'}")

async def download_engine(m, url, tag=None):
    status = await m.reply_text("⚙️ **Processing...**")
    cookie_file = f"temp_{m.id}.txt"
    
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': f'{DOWNLOAD_DIR}/{m.id}_%(title)s.%(ext)s',
        'merge_output_format': 'mp4',
        'quiet': True,
    }

    if tag:
        data = await cookies_col.find_one({"tag": tag})
        if data:
            with open(cookie_file, "w") as f: f.write(data["content"])
            ydl_opts['cookiefile'] = cookie_file
        else:
            return await status.edit("❌ Invalid Tag")

    try:
        await status.edit("📥 **Downloading/Recording...**")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            f_path = ydl.prepare_filename(info)

        await status.edit("📤 **Uploading to Telegram...**")
        start_up = time.time()
        await app.send_document(
            chat_id=m.chat.id,
            document=f_path,
            caption=f"✅ **Title:** {info.get('title')}\n🔗 [Source Link]({url})",
            progress=progress,
            progress_args=(status, start_up)
        )
        await update_stats()
        os.remove(f_path)
        await status.delete()
    except Exception as e:
        await status.edit(f"❌ **Error:** `{e}`")
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
    print("Bot is fully active.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())

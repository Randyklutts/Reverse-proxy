import os, json, subprocess
from aiohttp import web
import aiohttp

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ALLOWED_CHAT_ID  = os.getenv("CHAT_ID")

async def send(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        await s.post(url, json={
            "chat_id": chat_id, "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })

async def handle_webhook(request):
    data = await request.json()
    msg  = data.get("message", {})
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id")

    if str(chat_id) != str(ALLOWED_CHAT_ID):
        return web.Response(text="OK")

    if text == "/start":
        reply = ("🤖 *Reverse Proxy Bot*\n"
                 "• /traffic — latest traffic report\n"
                 "• /users — active logged-in sessions\n"
                 "• /seturl <url> — change target site\n"
                 "• /geturl — show current target")

    elif text == "/traffic":
        try:
            with open("/tmp/traffic_report.txt") as f:
                reply = f.read()
        except FileNotFoundError:
            reply = "No traffic data yet."

    elif text == "/users":
        try:
            with open("/tmp/sessions.json") as f:
                data_s = json.load(f)
        except Exception:
            data_s = {}
        if not data_s:
            reply = "No active sessions."
        else:
            lines = ["👥 *Active Sessions*"]
            for sid, rec in list(data_s.items())[:20]:
                lines.append(
                    f"• `{rec['user']}` @ `{rec['ip']}` "
                    f"({rec['hits']} req, {sid[:8]}...)"
                )
            reply = "\n".join(lines)

    elif text.startswith("/seturl"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].startswith("http"):
            reply = "Usage: `/seturl https://new-site.com`"
        else:
            new_url = parts[1].strip()
            result = subprocess.run(
                ["/app/set_target.sh", new_url],
                capture_output=True, text=True
            )
            reply = (f"✅ Target set to `{new_url}`" if result.returncode == 0
                     else f"❌ Failed: {result.stderr}")

    elif text == "/geturl":
        try:
            with open("/tmp/target_url.txt") as f:
                reply = f"🎯 Current target: `{f.read().strip()}`"
        except FileNotFoundError:
            reply = "Unknown."

    else:
        reply = f"Unknown command: {text}"

    await send(chat_id, reply)
    return web.Response(text="OK")

app = web.Application()
app.router.add_post('/webhook/', handle_webhook)

if __name__ == "__main__":
    port = int(os.getenv("BOT_PORT", 8080))
    web.run_app(app, host="127.0.0.1", port=port)

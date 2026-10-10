import os, json, subprocess
from aiohttp import web
import aiohttp

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ALLOWED_CHAT_ID  = os.getenv("CHAT_ID")


async def send(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        try:
            await s.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=10)
        except Exception as e:
            print("Telegram send failed:", e)


def _load_sessions():
    """Return the sessions dict regardless of state-file format."""
    try:
        with open("/tmp/sessions.json") as f:
            data = json.load(f)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    # New format: {"sessions": {...}, "seen_users": {...}}
    if "sessions" in data and isinstance(data["sessions"], dict):
        return data["sessions"]

    # Old format: {"sess_id": {...}}
    return data


def _fmt_users_reply(sessions):
    if not sessions:
        return "No active sessions."

    lines = ["👥 *Active Sessions*"]
    shown = 0
    for sid, rec in sessions.items():
        if not isinstance(rec, dict):
            continue
        user = rec.get("user", "(unknown)")
        ip   = rec.get("ip", "?")
        hits = rec.get("hits", 0)
        tag  = "❓" if rec.get("anon_session") else "👤"
        lines.append(
            f"{tag} `{user}` @ `{ip}` "
            f"({hits} req, `{sid[:8]}...`)"
        )
        shown += 1
        if shown >= 20:
            break

    return "\n".join(lines) if shown else "No active sessions."


async def handle_webhook(request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(text="OK")

    msg  = data.get("message", {})
    text = msg.get("text", "") or ""
    chat_id = msg.get("chat", {}).get("id")

    if str(chat_id) != str(ALLOWED_CHAT_ID):
        return web.Response(text="OK")

    try:
        if text == "/start":
            reply = ("🤖 *Reverse Proxy Bot*\n"
                     "• /traffic — latest traffic report\n"
                     "• /users — active logged-in sessions\n"
                     "• /seturl <url> — change target site\n"
                     "• /geturl — show current target")

        elif text == "/traffic":
            try:
                with open("/tmp/traffic_report.txt") as f:
                    reply = f.read() or "No traffic data yet."
            except FileNotFoundError:
                reply = "No traffic data yet."

        elif text == "/users":
            reply = _fmt_users_reply(_load_sessions())

        elif text.startswith("/seturl"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].startswith("http"):
                reply = "Usage: `/seturl https://new-site.com`"
            else:
                new_url = parts[1].strip()
                result = subprocess.run(
                    ["/app/set_target.sh", new_url],
                    capture_output=True, text=True, timeout=15
                )
                reply = (f"✅ Target set to `{new_url}`"
                         if result.returncode == 0
                         else f"❌ Failed: {result.stderr.strip()[:200]}")

        elif text == "/geturl":
            try:
                with open("/tmp/target_url.txt") as f:
                    reply = f"🎯 Current target: `{f.read().strip()}`"
            except FileNotFoundError:
                reply = "Unknown."

        else:
            reply = f"Unknown command: {text}"

    except Exception as e:
        # Never let a handler crash the webhook
        reply = f"⚠️ Error: `{type(e).__name__}: {str(e)[:150]}`"

    await send(chat_id, reply)
    return web.Response(text="OK")


app = web.Application()
app.router.add_post('/webhook/', handle_webhook)

if __name__ == "__main__":
    port = int(os.getenv("BOT_PORT", 8080))
    web.run_app(app, host="127.0.0.1", port=port)

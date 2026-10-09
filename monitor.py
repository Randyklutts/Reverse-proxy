import os, re, json, time, asyncio, aiohttp
from collections import Counter

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")
LOG_FILE       = "/var/log/nginx/access.log"

NOTIFY_ON_LOGIN   = os.getenv("NOTIFY_ON_LOGIN", "true").lower() == "true"
NOTIFY_ON_LOGOUT  = os.getenv("NOTIFY_ON_LOGOUT", "true").lower() == "true"
REPORT_INTERVAL   = int(os.getenv("REPORT_INTERVAL", 300))   # 0 = disabled
ONLY_LOGGED_IN    = os.getenv("ONLY_LOGGED_IN_REPORTS", "true").lower() == "true"
LOGOUT_GRACE      = int(os.getenv("LOGOUT_GRACE", 3))
STATE_FILE        = "/tmp/sessions.json"

LOG_PATTERN = re.compile(
    r'(?P<ip>[^|]+)\|(?P<time>[^|]+)\|"(?P<request>[^"]+)"\|'
    r'(?P<status>\d+)\|(?P<bytes>\d+)\|"(?P<ua>[^"]*)"\|'
    r'"(?P<xfwd>[^"]*)"\|"(?P<session>[^"]*)"\|"(?P<user>[^"]*)"'
)

sessions = {}

def load_state():
    global sessions
    try:
        with open(STATE_FILE) as f:
            sessions = json.load(f)
    except Exception:
        sessions = {}

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception:
        pass

async def send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        try:
            await s.post(url, json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=10)
        except Exception as e:
            print("Telegram send failed:", e)

def fmt_login(sess_id, rec, path):
    return (
        "🔐 *New Login*\n"
        f"👤 User: `{rec['user']}`\n"
        f"🔑 Session: `{sess_id[:16]}...`\n"
        f"🌍 IP: `{rec['ip']}`\n"
        f"📄 Path: `{path}`\n"
        f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rec['login_ts']))}\n"
        f"🖥 `{rec['ua'][:80]}`"
    )

def fmt_logout(sess_id, rec):
    dur = int(time.time() - rec["login_ts"])
    return (
        "🚪 *Logout*\n"
        f"👤 User: `{rec['user']}`\n"
        f"🔑 Session: `{sess_id[:16]}...`\n"
        f"⏱ Duration: {dur // 60}m {dur % 60}s\n"
        f"📨 Requests: {rec['hits']}\n"
        f"💾 Bytes: {rec['bytes']:,}"
    )

async def process_line(line):
    m = LOG_PATTERN.match(line.strip())
    if not m:
        return
    g = m.groupdict()

    ip     = g["ip"]
    user   = g["user"] or ""
    sess   = g["session"] or ""
    nbytes = int(g["bytes"])
    req    = g["request"].split()
    path   = req[1] if len(req) >= 2 else "/"

    # LOGIN: username + session both present
    if user and sess:
        rec = sessions.get(sess)
        if rec is None or rec["user"] != user:
            sessions[sess] = {
                "user": user, "ip": ip, "ua": g["ua"],
                "login_ts": time.time(), "last_ts": time.time(),
                "hits": 1, "bytes": nbytes, "anon_streak": 0,
            }
            save_state()
            if NOTIFY_ON_LOGIN:
                await send(fmt_login(sess, sessions[sess], path))
        else:
            rec["last_ts"] = time.time()
            rec["hits"] += 1
            rec["bytes"] += nbytes
            rec["anon_streak"] = 0

    # LOGOUT: known session, username gone
    elif sess and sess in sessions:
        rec = sessions[sess]
        rec["anon_streak"] = rec.get("anon_streak", 0) + 1
        if rec["anon_streak"] >= LOGOUT_GRACE:
            if NOTIFY_ON_LOGOUT:
                await send(fmt_logout(sess, rec))
            del sessions[sess]
            save_state()

async def send_periodic_report():
    active = {k: v for k, v in sessions.items() if v["user"]}
    if ONLY_LOGGED_IN and not active:
        return

    users     = Counter(v["user"] for v in active.values())
    total_req = sum(v["hits"] for v in active.values())
    total_byt = sum(v["bytes"] for v in active.values())

    lines = [
        "📊 *Active Logged-in Sessions*",
        f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"👥 Logged-in users: {len(active)}",
        f"👤 Unique accounts: {len(users)}",
        f"📨 Requests (logged-in only): {total_req}",
        f"💾 Bytes: {total_byt:,}",
        "",
        "*Per user:*",
    ]
    for u, c in users.most_common(10):
        lines.append(f"• `{u}` — {c} session(s)")

    await send("\n".join(lines))

async def monitor():
    while not os.path.exists(LOG_FILE):
        await asyncio.sleep(2)

    load_state()
    last_report = time.time()

    with open(LOG_FILE, errors="ignore") as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if line:
                await process_line(line)
            else:
                await asyncio.sleep(0.5)

            if REPORT_INTERVAL > 0 and time.time() - last_report >= REPORT_INTERVAL:
                await send_periodic_report()
                last_report = time.time()

if __name__ == "__main__":
    asyncio.run(monitor())

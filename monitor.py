import os, re, json, time, asyncio, aiohttp
from collections import Counter

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")
LOG_FILE       = "/var/log/nginx/access.log"

NOTIFY_ON_LOGIN       = os.getenv("NOTIFY_ON_LOGIN", "true").lower() == "true"
NOTIFY_ON_LOGOUT      = os.getenv("NOTIFY_ON_LOGOUT", "true").lower() == "true"
REPORT_INTERVAL       = int(os.getenv("REPORT_INTERVAL", 300))       # 0 = disabled
ONLY_LOGGED_IN        = os.getenv("ONLY_LOGGED_IN_REPORTS", "true").lower() == "true"
LOGOUT_GRACE          = int(os.getenv("LOGOUT_GRACE", 3))
DETECT_ANON_SESSIONS  = os.getenv("DETECT_ANON_SESSIONS", "false").lower() == "true"
DEDUP_LOGIN_WINDOW    = int(os.getenv("DEDUP_LOGIN_WINDOW", 0))       # seconds; 0 = off
STATE_FILE            = os.getenv("STATE_FILE", "/tmp/sessions.json")

# 9 pipe-delimited fields written by nginx.conf.template
LOG_PATTERN = re.compile(
    r'(?P<ip>[^|]+)\|(?P<time>[^|]+)\|"(?P<request>[^"]+)"\|'
    r'(?P<status>\d+)\|(?P<bytes>\d+)\|"(?P<ua>[^"]*)"\|'
    r'"(?P<xfwd>[^"]*)"\|"(?P<session>[^"]*)"\|"(?P<user>[^"]*)"'
)

sessions = {}      # sess_id  -> session record
seen_users = {}    # username -> last_login_ts (for DEDUP_LOGIN_WINDOW)


# ─────────────────────────────────────────────────────────── persistence
def load_state():
    global sessions, seen_users
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        sessions   = data.get("sessions", {})
        seen_users = data.get("seen_users", {})
    except Exception:
        sessions, seen_users = {}, {}


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"sessions": sessions, "seen_users": seen_users}, f)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────── telegram
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


# ─────────────────────────────────────────────────────────── formatters
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


def fmt_anon_session(sess_id, ip, path, ua):
    return (
        "🔐 *Session Detected (pre-existing)*\n"
        "👤 User: `(unknown — no username cookie)`\n"
        f"🔑 Session: `{sess_id[:16]}...`\n"
        f"🌍 IP: `{ip}`\n"
        f"📄 Path: `{path}`\n"
        f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🖥 `{ua[:80]}`"
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


# ─────────────────────────────────────────────────────────── helpers
def should_notify_login(user, now):
    """Respect DEDUP_LOGIN_WINDOW if set."""
    if DEDUP_LOGIN_WINDOW <= 0:
        return True
    last = seen_users.get(user, 0)
    if now - last < DEDUP_LOGIN_WINDOW:
        return False
    seen_users[user] = now
    return True


# ─────────────────────────────────────────────────────────── core
async def process_line(line):
    m = LOG_PATTERN.match(line.strip())
    if not m:
        return
    g = m.groupdict()

    ip     = g["ip"]
    user   = (g["user"] or "").strip()
    sess   = (g["session"] or "").strip()
    nbytes = int(g["bytes"])
    req    = g["request"].split()
    path   = req[1] if len(req) >= 2 else "/"
    now    = time.time()

    # ───────── LOGIN: both cookies present ─────────
    if user and sess:
        rec = sessions.get(sess)
        if rec is None or rec.get("user") != user:
            sessions[sess] = {
                "user": user, "ip": ip, "ua": g["ua"],
                "login_ts": now, "last_ts": now,
                "hits": 1, "bytes": nbytes, "anon_streak": 0,
                "anon_session": False,
            }
            save_state()
            if NOTIFY_ON_LOGIN and should_notify_login(user, now):
                await send(fmt_login(sess, sessions[sess], path))
        else:
            rec["last_ts"] = now
            rec["hits"]   += 1
            rec["bytes"]  += nbytes
            rec["anon_streak"] = 0
            save_state()

    # ───────── ANON SESSION: session cookie but no username ─────────
    elif sess and sess not in sessions and DETECT_ANON_SESSIONS:
        sessions[sess] = {
            "user": "(unknown)", "ip": ip, "ua": g["ua"],
            "login_ts": now, "last_ts": now,
            "hits": 1, "bytes": nbytes, "anon_streak": 0,
            "anon_session": True,
        }
        save_state()
        if NOTIFY_ON_LOGIN:
            await send(fmt_anon_session(sess, ip, path, g["ua"]))

    # ───────── Existing session, username present ─────────
    elif sess and sess in sessions and user:
        rec = sessions[sess]
        rec["last_ts"] = now
        rec["hits"]   += 1
        rec["bytes"]  += nbytes
        rec["anon_streak"] = 0
        # upgrade an anon session to a named user if username appears
        if rec.get("anon_session") and user:
            rec["user"] = user
            rec["anon_session"] = False
        save_state()

    # ───────── LOGOUT: known session, username gone ─────────
    elif sess and sess in sessions and not user:
        rec = sessions[sess]
        rec["anon_streak"] = rec.get("anon_streak", 0) + 1
        if rec["anon_streak"] >= LOGOUT_GRACE:
            if NOTIFY_ON_LOGOUT and not rec.get("anon_session"):
                await send(fmt_logout(sess, rec))
            del sessions[sess]
            save_state()


# ─────────────────────────────────────────────────────────── periodic report
async def send_periodic_report():
    active = {k: v for k, v in sessions.items() if v["user"] and v["user"] != "(unknown)"}
    if ONLY_LOGGED_IN and not active:
        return

    users     = Counter(v["user"] for v in active.values())
    total_req = sum(v["hits"]  for v in active.values())
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

    # also surface anonymous sessions briefly
    anon = [k for k, v in sessions.items() if v.get("anon_session")]
    if anon:
        lines.append("")
        lines.append(f"❓ Anonymous sessions: {len(anon)}")

    await send("\n".join(lines))


# ─────────────────────────────────────────────────────────── main loop
async def monitor():
    while not os.path.exists(LOG_FILE):
        await asyncio.sleep(2)

    load_state()
    last_report = time.time()

    with open(LOG_FILE, errors="ignore") as f:
        f.seek(0, 2)   # start at end-of-file: only new lines from now on
        while True:
            line = f.readline()
            if line:
                try:
                    await process_line(line)
                except Exception as e:
                    print("process_line error:", e)
            else:
                await asyncio.sleep(0.5)

            if REPORT_INTERVAL > 0 and time.time() - last_report >= REPORT_INTERVAL:
                try:
                    await send_periodic_report()
                except Exception as e:
                    print("periodic report error:", e)
                last_report = time.time()


if __name__ == "__main__":
    asyncio.run(monitor())

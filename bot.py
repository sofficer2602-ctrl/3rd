import asyncio
import json
import os
import re
import time
import hashlib
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
import aiohttp

# ========== VERSION — check logs to confirm which code is running ==========
VERSION = "v4.1 - Smart Fast Engine"

BOT_TOKEN       = os.getenv("BOT_TOKEN", "8885622806:AAEzNbdnJJWd5AGC6pC8LUBcOs2SRzKXlds")
CHANNEL_ID      = os.getenv("CHANNEL_ID", "-1004427004477")
NEW_CHANNEL_ID  = os.getenv("NEW_CHANNEL_ID", "-1003250473765")
PRIVATE_CHANNEL_ID = os.getenv("PRIVATE_CHANNEL_ID", "-1003956267456")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "8473160748"))
PANEL_USER      = os.getenv("PANEL_USER", "5260101")
PANEL_PASS      = os.getenv("PANEL_PASS", "Shoaibpanel@123!!!")
LOGIN_URL       = "https://mysmsportal.com/index.php?login=1"
OTP_SUMMARY_URL = "https://mysmsportal.com/index.php?opt=shw_sts_today"
DETAIL_URL      = "https://mysmsportal.com/index.php?opt=shw_sts_today_det"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "2"))

# ========== LINKS ==========
CHANNEL_URL    = "https://t.me/allnumbersfree"
GET_NUMBER_URL = "https://t.me/allnumbersfree"

seen_file = "seen_messages.json"
bot_ref = None
last_alert_time = 0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
}

# Summary row pattern: NUMBER, SENDER, MSG_COUNT, RANGE, then form with ddi+oad
# Example: <td>+59161156915</td><td>Dola</td><td>1</td><td>BOLIVIA</td><td><form...ddi=+59161156915...oad=Dola...></td>
summary_row_pattern = re.compile(
    r'<tr[^>]*class=table_line_[^>]*>'
    r'.*?<td[^>]*>(.*?)</td>'    # NUMBER
    r'.*?<td[^>]*>(.*?)</td>'    # SENDER
    r'.*?<td[^>]*>(.*?)</td>'    # MSG COUNT
    r'.*?<td[^>]*>(.*?)</td>'    # RANGE
    r'.*?name=ddi\s+value=([^\s>]+).*?name=oad\s+value=([^\s>]+)',
    re.DOTALL | re.IGNORECASE
)

msg_pattern = re.compile(
    r'<tr[^>]*class=table_line_[^>]*>'
    r'.*?<td[^>]*>(.*?)</td>'    # DATE TIME
    r'.*?<td[^>]*>(.*?)</td>'    # LONGCODE (phone)
    r'.*?<td[^>]*>(.*?)</td>'    # SENDER
    r'.*?<td[^>]*>(.*?)</td>'    # RANGE
    r'.*?<td[^>]*>(.*?)</td>'    # STATUS
    r'.*?<td[^>]*>(.*?)</td>',   # MESSAGE BODY
    re.DOTALL | re.IGNORECASE
)

# ========== BULLETPROOF DEDUP ==========
def make_dedup_key(ph, sender, msg_text):
    digits_ph = re.sub(r'\D', '', str(ph))
    all_codes = re.findall(r'\b(\d{4,8})\b', str(msg_text))
    codes_str = "|".join(sorted(all_codes)) if all_codes else ""
    raw = f"{digits_ph}:{sender}:{codes_str}"
    return hashlib.md5(raw.encode()).hexdigest()

def load_seen():
    try:
        with open(seen_file, 'r') as f:
            data = json.load(f)
            return {k: True for k in data[-1000:]}
    except:
        return {}

def save_seen(seen):
    try:
        with open(seen_file, 'w') as f:
            json.dump(list(seen.keys())[-1000:], f)
    except:
        pass

seen_messages = load_seen()

# ========== HELPERS ==========
def escape_markdown(text):
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = str(text).replace(char, f'\\{char}')
    return text

def region_to_flag(region):
    return "".join(chr(127397 + ord(c)) for c in region.upper())

def get_country(ph):
    try:
        import phonenumbers
        p = phonenumbers.parse("+" + str(ph).lstrip("+"))
        cc = "+" + str(p.country_code)
        region = phonenumbers.region_code_for_number(p)
        if region:
            return region_to_flag(region), cc
    except:
        pass
    return "🌐", "+???"

async def safe_send(bot, chat_id, text, reply_markup=None):
    try:
        if reply_markup:
            await bot.send_message(chat_id, text, parse_mode="MarkdownV2", reply_markup=reply_markup, read_timeout=10, write_timeout=10)
        else:
            await bot.send_message(chat_id, text, parse_mode="Markdown", read_timeout=10, write_timeout=10)
        return True
    except Exception as e:
        print(f"Send err [{chat_id}]: {e}", flush=True)
        return False

async def send_to_all_channels(text, reply_markup):
    tasks = [
        safe_send(bot_ref, CHANNEL_ID, text, reply_markup),
        safe_send(bot_ref, NEW_CHANNEL_ID, text, reply_markup),
        safe_send(bot_ref, PRIVATE_CHANNEL_ID, text, reply_markup),
    ]
    await asyncio.gather(*tasks)

async def admin_alert(bot, text):
    global last_alert_time
    now = time.time()
    if now - last_alert_time < 3600:
        return
    last_alert_time = now
    await safe_send(bot, ADMIN_ID, text)

def mask(p):
    p = p.strip()
    return p if len(p) <= 6 else f"{p[:4]}*****{p[-3:]}"

def extract_otp(t):
    m = re.search(r'\b(\d{4,8})\b', t)
    return m.group(1) if m else "N/A"

# ========== LOGIN FUNCTION ==========
async def do_login(session):
    try:
        print("🔐 Logging into MySMSPortal...", flush=True)
        data = {
            "user": PANEL_USER,
            "password": PANEL_PASS
        }
        async with session.post(LOGIN_URL, data=data, timeout=aiohttp.ClientTimeout(total=15)) as r:
            text = await r.text()
            if "Please enter your login details" not in text:
                print("✅ Login Successful!", flush=True)
                return True
            else:
                print("❌ Login Failed - check credentials", flush=True)
                return False
    except Exception as e:
        print(f"❌ Login Error: {e}", flush=True)
        return False

# ========== MAIN SCRAPING LOOP ==========
async def run_bot():
    global seen_messages

    print(f"🚀 BOT {VERSION} STARTING...", flush=True)
    await safe_send(bot_ref, ADMIN_ID, f"🚀 Bot {VERSION} started!")

    first_run = True
    err_count = 0
    # Track message count per number+sender to detect changes
    # Key: "ddi|oad" -> last known msg count
    last_msg_counts = {}

    conn = aiohttp.TCPConnector(limit=10, ssl=False)
    async with aiohttp.ClientSession(connector=conn, headers=HEADERS) as session:
        # Initial Login
        await do_login(session)

        while True:
            try:
                # 1. Fetch Summary Page (lightweight — just 1 request)
                async with session.get(OTP_SUMMARY_URL, timeout=aiohttp.ClientTimeout(total=12)) as r:
                    summary_html = await r.text()

                # Check if session expired
                if "Please enter your login details" in summary_html:
                    print("🔄 Session expired — re-logging in...", flush=True)
                    if not await do_login(session):
                        await asyncio.sleep(4)
                        continue
                    continue

                # 2. Parse summary rows: extract NUMBER, SENDER, MSG_COUNT, ddi, oad
                rows = summary_row_pattern.findall(summary_html)
                total = len(rows)
                print(f"📊 {total} numbers", flush=True)

                if not rows:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # 3. Find which numbers have NEW messages (count changed or new number)
                changed = []

                if first_run:
                    # ⚡ FIRST RUN: Don't fetch any details! Just save current counts.
                    # This makes boot INSTANT even with 500 numbers (0 detail requests)
                    for number, sender, msg_count_str, rng, ddi, oad in rows:
                        row_key = f"{ddi.strip()}|{oad.strip()}"
                        last_msg_counts[row_key] = msg_count_str.strip()
                    first_run = False
                    print(f"🔒 First scan done — tracking {len(last_msg_counts)} numbers (0 detail fetches needed)", flush=True)
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                for number, sender, msg_count_str, rng, ddi, oad in rows:
                    number = number.strip()
                    sender = sender.strip()
                    msg_count_str = msg_count_str.strip()
                    ddi = ddi.strip()
                    oad = oad.strip()
                    row_key = f"{ddi}|{oad}"

                    if last_msg_counts.get(row_key) != msg_count_str:
                        # Message count changed! New SMS arrived for this number
                        changed.append((ddi, oad, row_key, msg_count_str))

                if changed:
                    print(f"🔍 {len(changed)} numbers changed — fetching details...", flush=True)

                    # 4. Fetch changed numbers in BATCHES of 5 (safe for 100+ numbers)
                    BATCH_SIZE = 5

                    async def fetch_one(ddi, oad):
                        try:
                            async with session.post(DETAIL_URL, data={"ddi": ddi, "oad": oad}, timeout=aiohttp.ClientTimeout(total=12)) as r_det:
                                html = await r_det.text()
                                matches = msg_pattern.findall(html)
                                return ddi, oad, matches, True
                        except Exception as e:
                            print(f"⚠️ Fetch retry needed for {ddi}: {type(e).__name__}", flush=True)
                            return ddi, oad, [], False

                    results = []
                    for batch_start in range(0, len(changed), BATCH_SIZE):
                        batch = changed[batch_start:batch_start + BATCH_SIZE]
                        batch_tasks = [fetch_one(ddi, oad) for ddi, oad, _, _ in batch]
                        batch_results = await asyncio.gather(*batch_tasks)
                        results.extend(batch_results)

                    new_count = 0
                    for i, (ddi, oad, msgs, success) in enumerate(results):
                        row_key = changed[i][2]
                        msg_count_str = changed[i][3]

                        # Only update cache if fetch was successful
                        if success:
                            last_msg_counts[row_key] = msg_count_str

                        for m in msgs:
                            dt, ph, se, rg, st, ms = [x.strip() for x in m]

                            if not ms or len(ms) < 4:
                                continue
                            if ms.lower() in ['select', 'details', 'action']:
                                continue

                            actual_phone = ph if re.search(r'\+?\d{8,}', ph) else ddi
                            actual_sender = se if len(se) >= 2 else oad

                            key = make_dedup_key(actual_phone, actual_sender, ms)

                            if key not in seen_messages:
                                seen_messages[key] = True

                                if first_run and len(seen_messages) > 10:
                                    continue

                                new_count += 1
                                otp = extract_otp(ms)
                                masked = mask(actual_phone)

                                flag, ccode = get_country(actual_phone)
                                clean_masked = escape_markdown(masked)
                                clean_ccode = escape_markdown(ccode)
                                line1 = f"{flag} {clean_ccode} \\| 🟢 {clean_masked} \\#EN"

                                clean_sender = escape_markdown(actual_sender)
                                if otp and otp != "N/A":
                                    clean_otp = escape_markdown(otp)
                                    line2 = f"🔥 {clean_sender} \\- `{clean_otp}`"
                                else:
                                    line2 = f"🔥 {clean_sender} \\- SMS Received"

                                message_text = f"{line1}\n{line2}"

                                if otp and otp != "N/A":
                                    otp_btn = InlineKeyboardButton(f"🛡️ {otp}", api_kwargs={'copy_text': {'text': str(otp)}})
                                else:
                                    otp_btn = InlineKeyboardButton("🛡️ SMS", callback_data="ignore")

                                keyboard = [
                                    [InlineKeyboardButton("🔔 Channel", url=CHANNEL_URL), otp_btn],
                                    [InlineKeyboardButton("📞 Get Number", url=GET_NUMBER_URL)]
                                ]
                                reply_markup = InlineKeyboardMarkup(keyboard)

                                await send_to_all_channels(message_text, reply_markup)
                                print(f"✅ SENT: {masked} | {otp} [key={key[:8]}]", flush=True)

                    if new_count > 0:
                        save_seen(seen_messages)
                else:
                    # Also update counts for rows that haven't changed (keep cache fresh)
                    for number, sender, msg_count_str, rng, ddi, oad in rows:
                        row_key = f"{ddi.strip()}|{oad.strip()}"
                        last_msg_counts[row_key] = msg_count_str.strip()

                if first_run:
                    first_run = False
                    # On first run, mark all current counts
                    for number, sender, msg_count_str, rng, ddi, oad in rows:
                        row_key = f"{ddi.strip()}|{oad.strip()}"
                        last_msg_counts[row_key] = msg_count_str.strip()
                    save_seen(seen_messages)
                    print(f"🔒 First scan done — {len(seen_messages)} msgs marked, tracking {len(last_msg_counts)} numbers", flush=True)

                if len(seen_messages) > 1500:
                    keys_to_keep = list(seen_messages.keys())[-1000:]
                    seen_messages = {k: True for k in keys_to_keep}

                err_count = 0

            except Exception as e:
                err_count += 1
                print(f"Poll err ({err_count}/5): {e}", flush=True)
                if err_count >= 5:
                    await admin_alert(bot_ref, f"⚠️ Polling errors: {e}")
                    err_count = 0
                    await asyncio.sleep(3)

            await asyncio.sleep(POLL_INTERVAL)

# ========== BACKGROUND WEB SERVER FOR HEALTH CHECKS ==========
def start_simple_server():
    import http.server
    import socketserver
    port = int(os.getenv('PORT', 8080))

    class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
            print(f"🌐 Health server running on port {port}", flush=True)
            httpd.serve_forever()
    except Exception as e:
        print(f"Health server note: {e}", flush=True)

async def web_server():
    await asyncio.to_thread(start_simple_server)

async def main():
    global bot_ref
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing!", flush=True)
        return

    app = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    bot_ref = app.bot

    await app.initialize()
    await app.start()

    print(f"✅ Bot {VERSION} initialized", flush=True)

    asyncio.create_task(web_server())
    asyncio.create_task(run_bot())
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("STOPPED", flush=True)

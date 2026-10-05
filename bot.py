import asyncio
import json
import os
import re
import time
import hashlib
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from playwright.async_api import async_playwright

# ========== VERSION — check logs to confirm which code is running ==========
VERSION = "v3.0"

BOT_TOKEN       = os.getenv("BOT_TOKEN", "8885622806:AAEzNbdnJJWd5AGC6pC8LUBcOs2SRzKXlds")
CHANNEL_ID      = os.getenv("CHANNEL_ID", "-1004427004477")
NEW_CHANNEL_ID  = os.getenv("NEW_CHANNEL_ID", "-1003250473765")
PRIVATE_CHANNEL_ID = os.getenv("PRIVATE_CHANNEL_ID", "-1003956267456")
ADMIN_ID        = int(os.getenv("ADMIN_ID", "8473160748"))
PANEL_USER      = os.getenv("PANEL_USER", "5260101")
PANEL_PASS      = os.getenv("PANEL_PASS", "Shoaibpanel@123!!!")
LOGIN_URL       = "https://mysmsportal.com/index.php"
OTP_SUMMARY_URL = "https://mysmsportal.com/index.php?opt=shw_sts_today"
POLL_INTERVAL   = int(os.getenv("POLL_INTERVAL", "2"))

# ========== LINKS ==========
CHANNEL_URL    = "https://t.me/allnumbersfree"
GET_NUMBER_URL = "https://t.me/allnumbersfree"

cookies_file = "panel_cookies.json"
seen_file = "seen_messages.json"
bot_ref = None
last_alert_time = 0

# Browser state
pw = None
browser = None
context = None
page = None

# ========== BULLETPROOF DEDUP ==========
# Key = hash of (digits-only-phone + sender + first 4-8 digit code in message)
# This NEVER changes no matter what the panel does to timestamps/formatting

def make_dedup_key(ph, sender, msg_text):
    """Create a stable dedup key that won't change even if panel updates timestamps"""
    digits_ph = re.sub(r'\D', '', str(ph))
    # Extract ALL 4-8 digit numbers from message to use as fingerprint
    all_codes = re.findall(r'\b(\d{4,8})\b', str(msg_text))
    codes_str = "|".join(sorted(all_codes)) if all_codes else ""
    # Use sender + phone + codes as the unique fingerprint
    raw = f"{digits_ph}:{sender}:{codes_str}"
    return hashlib.md5(raw.encode()).hexdigest()

def load_seen():
    try:
        with open(seen_file, 'r') as f:
            data = json.load(f)
            return {k: True for k in data[-800:]}
    except:
        return {}

def save_seen(seen):
    try:
        with open(seen_file, 'w') as f:
            json.dump(list(seen.keys())[-800:], f)
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

# ========== BROWSER ==========
async def start_browser():
    global pw, browser, context, page
    try:
        if page: await page.close()
        if context: await context.close()
        if browser: await browser.close()
        if pw: await pw.stop()
    except: pass

    # 🛑 FORCE KILL ANY ORPHANED PROCESSES TO FREE RAM!
    import os
    os.system("pkill -9 -f chrome")
    os.system("pkill -9 -f chromium")
    os.system("pkill -9 -f playwright")
    await asyncio.sleep(2)

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
            '--disable-gpu', '--disable-software-rasterizer', '--disable-extensions',
            '--disable-default-apps', '--disable-sync', '--disable-translate',
            '--disable-background-networking', '--disable-background-timer-throttling',
            '--disable-renderer-backgrounding', '--disable-blink-features=AutomationControlled',
            '--mute-audio', '--no-first-run', '--no-zygote',
            '--memory-pressure-off', '--single-process',
            '--aggressive-cache-discard', '--max_old_space_size=256',
        ]
    )
    context = await browser.new_context(
        viewport={'width': 1024, 'height': 768},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        locale='en-US', device_scale_factor=1, is_mobile=False, has_touch=False, java_script_enabled=True
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        window.chrome = {runtime: {}};
    """)
    await context.route("**/*", lambda route:
        route.abort() if route.request.resource_type in ['image', 'stylesheet', 'font', 'media'] else route.continue_()
    )
    page = await context.new_page()
    try:
        with open(cookies_file) as f:
            await context.add_cookies(json.load(f))
    except: pass
    print("🌐 Browser started", flush=True)

async def save_cookies():
    try:
        with open(cookies_file, 'w') as f:
            json.dump(await context.cookies(), f)
    except: pass

async def do_login():
    try:
        print("🔐 Logging in...", flush=True)
        await page.goto(LOGIN_URL, timeout=25000, wait_until='domcontentloaded')
        await asyncio.sleep(0.8)
        u = page.locator('input[type="text"]').first
        await u.click(); await u.fill(PANEL_USER)
        await asyncio.sleep(0.2)
        p = page.locator('input[type="password"]').first
        await p.click(); await p.fill(PANEL_PASS)
        await asyncio.sleep(0.2)
        await page.locator('button, input[type="submit"]').first.click()
        await asyncio.sleep(1.5)
        await save_cookies()
        print("✅ Login OK", flush=True)
        return True
    except Exception as e:
        print(f"❌ Login failed: {e}", flush=True)
        return False

# ========== MAIN LOOP ==========
async def run_bot():
    global seen_messages

    await start_browser()

    try:
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(0.5)
        content = await page.content()
        if 'Please enter your login details' in content:
            await do_login()
            await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
    except:
        await do_login()
        await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')

    print(f"✅ BOT {VERSION} ONLINE — Har {POLL_INTERVAL}s check ⚡", flush=True)
    await safe_send(bot_ref, ADMIN_ID, f"✅ Bot {VERSION} chalu — har {POLL_INTERVAL}s check!")

    err_count = 0
    crash_count = 0
    first_run = True
    visited_numbers = set()  # Track which numbers we already clicked this cycle
    summary_state = {}       # Track the exact text of each summary row to detect changes
    loop_count = 0

    while True:
        try:
            try:
                if first_run:
                    pass
                else:
                    await page.reload(timeout=15000, wait_until='domcontentloaded')
            except Exception as e:
                if 'crashed' in str(e).lower() or 'closed' in str(e).lower():
                    crash_count += 1
                    print(f"💥 Crash ({crash_count})", flush=True)
                    await start_browser()
                    await do_login()
                    await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
                    if crash_count >= 3:
                        await admin_alert(bot_ref, f"⚠️ Crash ({crash_count}x)")
                        crash_count = 0
                    continue
                raise

            crash_count = 0

            content = await page.content()
            if 'Please enter your login details' in content:
                print("🔄 Re-login", flush=True)
                if not await do_login():
                    await asyncio.sleep(3)
                    continue
                await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')

            rows = page.locator('table tbody tr')
            total = await rows.count()
            print(f"📊 {total} rows", flush=True)

            # Collect all summary numbers FIRST, then click only unique ones
            summary_numbers = []
            for i in range(total):
                row = rows.nth(i)
                cols = row.locator('td')
                if await cols.count() < 5:
                    continue
                
                row_text = (await row.inner_text()).strip()
                num = (await cols.nth(0).inner_text()).strip()
                num_digits = re.sub(r'\D', '', num)
                summary_numbers.append((i, num, num_digits, row_text))

            visited_numbers.clear()

            for idx, num, num_digits, row_text in summary_numbers:
                # ⚡ OPTIMIZATION: If the row text (including total SMS count) hasn't changed, skip clicking!
                if summary_state.get(num_digits) == row_text:
                    continue
                summary_state[num_digits] = row_text

                # SKIP if we already visited this number in this cycle
                if num_digits in visited_numbers:
                    continue
                visited_numbers.add(num_digits)

                # Re-locate rows fresh each time (page might have changed after go_back)
                rows = page.locator('table tbody tr')
                if idx >= await rows.count():
                    continue
                row = rows.nth(idx)

                form = row.locator('form').first
                clicked = False
                if await form.count() > 0:
                    try: await form.click(); clicked = True
                    except: pass
                if not clicked:
                    btn = row.locator('button:has-text("Select"), input[value*="Select"]').first
                    if await btn.count() > 0:
                        try: await btn.click(); clicked = True
                        except: pass
                if not clicked:
                    continue

                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=8000)
                    detail_rows = page.locator('table tbody tr')
                    new_count = 0
                    for j in range(await detail_rows.count()):
                        dcols = detail_rows.nth(j).locator('td')
                        if await dcols.count() >= 5:
                            dt = (await dcols.nth(0).inner_text()).strip()
                            ph = (await dcols.nth(1).inner_text()).strip()
                            se = (await dcols.nth(2).inner_text()).strip()
                            ms = (await dcols.nth(-1).inner_text()).strip()
                            if ms and len(ms) > 3 and dt:
                                # BULLETPROOF dedup key — immune to timestamp changes
                                key = make_dedup_key(ph, se, ms)

                                if key not in seen_messages:
                                    seen_messages[key] = True

                                    if first_run:
                                        continue

                                    new_count += 1
                                    otp = extract_otp(ms)
                                    masked = mask(ph)

                                    flag, ccode = get_country(ph)
                                    clean_masked = escape_markdown(masked)
                                    clean_ccode = escape_markdown(ccode)
                                    line1 = f"{flag} {clean_ccode} \\| 🟢 {clean_masked} \\#EN"

                                    clean_sender = escape_markdown(se)
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

                    # Go back to summary — use goto instead of go_back for reliability
                    await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                except Exception as e:
                    print(f"Detail err: {e}", flush=True)
                    try: await page.goto(OTP_SUMMARY_URL, timeout=15000, wait_until='domcontentloaded')
                    except: pass

            if first_run:
                first_run = False
                save_seen(seen_messages)
                print(f"🔒 First scan done — {len(seen_messages)} marked", flush=True)

            if len(seen_messages) > 1000:
                keys_to_keep = list(seen_messages.keys())[-800:]
                seen_messages = {k: True for k in keys_to_keep}

            err_count = 0

        except Exception as e:
            err_count += 1
            print(f"Poll err ({err_count}/5): {e}", flush=True)
            if 'crashed' in str(e).lower() or 'closed' in str(e).lower():
                await start_browser()
                await do_login()
                try: await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
                except: pass
            if err_count >= 5:
                await admin_alert(bot_ref, "⚠️ Errors — recovering...")
                err_count = 0
                await start_browser()
                await do_login()
                try: await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
                except: pass

        loop_count += 1
        if loop_count >= 200:  # Restart browser every ~10 minutes to prevent RAM Full (OOM)
            loop_count = 0
            print("🧹 Cleaning memory (Prevent Crash)...", flush=True)
            await start_browser()
            await do_login()
            try: await page.goto(OTP_SUMMARY_URL, timeout=20000, wait_until='domcontentloaded')
            except: pass

        await asyncio.sleep(POLL_INTERVAL)

def start_simple_server():
    import http.server
    import socketserver
    import os
    port = int(os.getenv('PORT', 8080))
    
    class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
            
        def log_message(self, format, *args):
            pass # Disable logging to keep console clean

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
        print(f"🌐 Web server started on port {port}", flush=True)
        httpd.serve_forever()

async def web_server():
    # Run simple server in a separate thread so it doesn't block asyncio
    await asyncio.to_thread(start_simple_server)

async def main():
    global bot_ref
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing!", flush=True); return

    app = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).build()
    bot_ref = app.bot

    await app.initialize()
    await app.start()

    print(f"✅ Bot {VERSION} SEND-ONLY mode", flush=True)

    # Start the background web server for Render health checks
    asyncio.create_task(web_server())

    asyncio.create_task(run_bot())
    await asyncio.Event().wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("STOPPED", flush=True)

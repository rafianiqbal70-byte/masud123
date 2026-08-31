import logging
import os
import sys
import asyncio
import re
import httpx
import hashlib
import json
import html
from io import StringIO, BytesIO
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes, 
    CallbackQueryHandler
)

# =========================================================================
# --- WINDOWS TERMINAL UNICODE FIX & ADVANCED LOGGING ---
# =========================================================================
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_core_debug.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# =========================================================================
# --- LOCAL FILE STORAGE DATABASE CONFIGURATION ---
# =========================================================================
DB_FILE = "bot_local_database.json"

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load DB file: {e}")
    return {
        "users": {},
        "stock": {},
        "assigned_stocks": {},
        "history": [],
        "withdrawals": {},
        "settings": {
            "otp_rate": "1.0",
            "min_withdraw": "1000",
            "global_cc_limit": "3",
            "default_user_stock_limit": "200",
            "api_status_Main": "1"
        },
        "country_rates": {},
        "country_limits": {},
        "country_user_limits": {},
        "processed_messages": {}
    }

db_data = load_db()
if "country_user_limits" not in db_data:
    db_data["country_user_limits"] = {}

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Failed to save DB file: {e}")

CACHE_USERS = db_data["users"]
CACHE_STOCK = db_data["stock"]
CACHE_ASSIGNMENTS = db_data["assigned_stocks"]
CACHE_SETTINGS = db_data["settings"]
CACHE_RATES = db_data["country_rates"]
CACHE_LIMITS = db_data["country_limits"]
CACHE_COUNTRY_USER_LIMITS = db_data["country_user_limits"]
CACHE_PROCESSED = db_data["processed_messages"]
CACHE_WITHDRAWALS = db_data["withdrawals"]

otp_process_lock = asyncio.locks.Lock() if hasattr(asyncio, "locks") else asyncio.Lock()

# =========================================================================
# --- BOT CONFIGURATION & GLOBAL SETTINGS ---
# =========================================================================
ADMIN_IDS = [6138186135, 6482184149, 8255112295]
TOKEN = "8979357599:AAEzlAsC7UedQ9do74TlTx12STYLueb0e0k" 
TARGET_GROUP_IDS = [-1003852486016, -1004340389110]  
OTP_GROUP_LINK = "https://t.me/your_otp_group" 

LOGIN_URL = "http://151.80.19.204/ints/login"
TARGET_URL = "http://151.80.19.204/ints/agent/SMSCDRReports"
USERNAME = "Umair12"
PASSWORD = "Arfat44#"

processed_ids = set()

http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(5.0, connect=2.0, read=4.0), 
    limits=httpx.Limits(max_connections=500, max_keepalive_connections=100),
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "application/json"
    }
)

def init_default_settings():
    defaults = {
        'otp_rate': '1.0',
        'min_withdraw': '1000',
        'global_cc_limit': '3',
        'default_user_stock_limit': '200',
        'api_status_Main': '1'
    }
    for k, v in defaults.items():
        if k not in CACHE_SETTINGS:
            CACHE_SETTINGS[k] = v
    save_db()

init_default_settings()

COUNTRY_FLAGS = {
    "afghanistan": "\U0001f1e6\U0001f1eb", "ghana": "\U0001f1ec\U0001f1ed",
    "azerbaijan": "\U0001f1e6\U0001f1ff", "bangladesh": "\U0001f1e7\U0001f1e9",
    "india": "\U0001f1ee\U0001f1f3", "pakistan": "\U0001f1f5\U0001f1f0",
    "russia": "\U0001f1f7\U0001f1fa", "lebanon": "\U0001f1f1\U0001f1e7",
    "nigeria": "\U0001f1f3\U0001f1ec"
}

def get_flag(country_name):
    clean_name = re.sub(r'[^a-z]', '', str(country_name).strip().lower())
    if not clean_name: return "\U0001f310"
    for k, v in COUNTRY_FLAGS.items():
        if re.sub(r'[^a-z]', '', k) == clean_name: return v
    return "\U0001f310"

async def get_config_val(key, default):
    return str(CACHE_SETTINGS.get(key, default))

async def set_config_val(key, value):
    CACHE_SETTINGS[key] = str(value)
    save_db()

async def get_country_payout(country_name):
    if country_name in CACHE_RATES: return float(CACHE_RATES[country_name])
    return float(await get_config_val('otp_rate', '1.0'))

async def get_country_cc_limit(country_name):
    if country_name in CACHE_LIMITS: return int(CACHE_LIMITS[country_name])
    return int(await get_config_val('global_cc_limit', '3'))

async def get_country_specific_user_limit(country_name):
    if country_name in CACHE_COUNTRY_USER_LIMITS: return int(CACHE_COUNTRY_USER_LIMITS[country_name])
    return int(await get_config_val('default_user_stock_limit', '200'))

def normalize_num(num_str):
    return re.sub(r'\D', '', str(num_str))

def parse_otp_body(text):
    if not text: return "N/A"
    match = re.search(r'\b(\d{4,8})\b', text)
    return match.group(1) if match else "N/A"

def rich_btn(text, style=None, callback_data=None, url=None, copy_text=None):
    btn = {"text": text}
    if style: btn["style"] = style 
    if callback_data: btn["callback_data"] = callback_data
    if url: btn["url"] = url
    if copy_text: btn["copy_text"] = {"text": copy_text}
    return btn

async def send_rich_message(bot, chat_id, text, keyboard_rows, parse_mode='HTML', **kwargs):
    payload = {
        "chat_id": chat_id, "text": text, "parse_mode": parse_mode,
        "reply_markup": {"inline_keyboard": keyboard_rows}
    }
    payload.update(kwargs)
    url = f"https://api.telegram.org/bot{bot.token}/sendMessage"
    try:
        resp = await http_client.post(url, json=payload)
        return resp.json()
    except Exception as e: logger.error(f"Dispatch failure: {e}")

async def edit_rich_message(bot, chat_id, message_id, text, keyboard_rows, parse_mode='HTML'):
    payload = {
        "chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode,
        "reply_markup": {"inline_keyboard": keyboard_rows}
    }
    url = f"https://api.telegram.org/bot{bot.token}/editMessageText"
    try:
        resp = await http_client.post(url, json=payload)
        return resp.json()
    except Exception as e: logger.error(f"Edit failure: {e}")

def build_admin_main():
    kb = [
        [KeyboardButton("📥 Number Upload"), KeyboardButton("🗑️ Clear Stock")],
        [KeyboardButton("👥 User List"), KeyboardButton("📊 Panel Stats")],
        [KeyboardButton("💸 Withdraw Requests"), KeyboardButton("💰 Total Paid")],
        [KeyboardButton("📢 Broadcast"), KeyboardButton("🚫 Ban User")],
        [KeyboardButton("✅ Unban User"), KeyboardButton("⚙️ Set CC Limit")],
        [KeyboardButton("💰 Country Rate Set"), KeyboardButton("📦 Country Stock Limit")],
        [KeyboardButton("/start")]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def build_user_main():
    kb = [
        [KeyboardButton("📱 Get Number 3"), KeyboardButton("📱 Get Number 10")],
        [KeyboardButton("💰 My Balance"), KeyboardButton("💸 Withdraw Funds")],
        [KeyboardButton("🏆 Leaderboard"), KeyboardButton("📦 Stock History")],
        [KeyboardButton("/start")]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

async def engine_assign_batch(user_id, country_name, count):
    try:
        skip_limit = await get_country_cc_limit(country_name)
        assigned_numbers = []
        ts_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        country_user_limit = await get_country_specific_user_limit(country_name)
        
        current_user_country_count = sum(1 for as_val in CACHE_ASSIGNMENTS.values() if isinstance(as_val, dict) and as_val.get('user_id') == user_id and as_val.get('country') == country_name and as_val.get('status') == 'assigned')
        if current_user_country_count >= country_user_limit:
            return None, f"⚠️ Limit reached ({country_user_limit})!"

        count = min(count, country_user_limit - current_user_country_count)

        for batch_id, b_data in list(CACHE_STOCK.items()):
            if isinstance(b_data, dict) and b_data.get("country") == country_name and b_data.get("numbers"):
                nums = b_data.get("numbers", [])
                if not nums: continue
                take_count = min(count - len(assigned_numbers), len(nums))
                taken = nums[:take_count]
                b_data["numbers"] = nums[take_count:]
                assigned_numbers.extend(taken)
                save_db()
                if len(assigned_numbers) >= count: break

        if not assigned_numbers: return None, "⚠️ Stock empty!"

        num_data_list = []
        for num in assigned_numbers:
            clean = normalize_num(num)
            short_val = clean[skip_limit:] if len(clean) > skip_limit else clean
            num_data_list.append({"full": clean, "short": short_val})
            
            assign_key = f"as_{clean}_{user_id}"
            CACHE_ASSIGNMENTS[assign_key] = {
                'user_id': user_id, 'number': clean, 'country': country_name,
                'assigned_at': ts_now, 'status': 'assigned'
            }
            save_db()

        header = f"📦 <b>Numbers Assigned for {get_flag(country_name)} {country_name}:</b>"
        return {"header": header, "numbers": num_data_list, "start_time": ts_now}, None
    except Exception as e:
        logger.error(f"Assignment core failure: {e}")
        return None, "❌ Allocation fail!"

async def engine_process_signal(application, record, silent=False):
    async with otp_process_lock:
        r_num = str(record.get('number', ''))
        r_msg = record.get('sms_text', '')
        service_val = record.get('service_val', 'Unknown')
        region = record.get('country_val', 'General')
        
        if not r_num or not r_msg or r_num == "0" or r_msg == "0": return False
        
        n_clean = normalize_num(r_num)
        m_hash = hashlib.md5(f"{n_clean}_{r_msg}".encode()).hexdigest()
        if m_hash in processed_ids: return False

        try:
            matched_assign = None
            for as_key, as_data in CACHE_ASSIGNMENTS.items():
                if isinstance(as_data, dict) and as_data.get('status') == 'assigned':
                    as_num = normalize_num(as_data.get('number', ''))
                    if as_num.endswith(n_clean[-8:]) if len(n_clean) >= 8 else as_num == n_clean:
                        matched_assign = as_data
                        break

            u_id = matched_assign.get('user_id') if matched_assign else None
            u_name = "System/Live"
            total_bal = 0.0
            payout = 0.0

            if u_id:
                u_data = CACHE_USERS.get(str(u_id), {})
                u_name = u_data.get('name', 'User') if isinstance(u_data, dict) else 'User'
                region = matched_assign.get('country', region)
                
                payout = await get_country_payout(region)
                curr_bal = float(u_data.get('balance', 0.0))
                total_bal = curr_bal + payout
                
                if not silent:
                    u_data['balance'] = total_bal
                    u_data['otp_count'] = int(u_data.get('otp_count', 0)) + 1
                    
                    if str(u_id) not in CACHE_PROCESSED or not isinstance(CACHE_PROCESSED[str(u_id)], dict):
                        CACHE_PROCESSED[str(u_id)] = {}
                    CACHE_PROCESSED[str(u_id)][m_hash] = {'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    save_db()

            processed_ids.add(m_hash)
            otp_code = parse_otp_body(r_msg)
            escaped_body = html.escape(r_msg)
            
            body = (
                f"<b>Earn With Jannat❤️</b>\n"
                f"🎉 <b>OTP Received Successfully</b> 🎉\n\n"
                f"📱 <b>Number:</b> <code>{r_num}</code>\n"
                f"🔑 <b>OTP Code:</b> <code>{otp_code}</code>\n"
                f"💼 <b>Service:</b> {service_val}\n"
                f"🌍 <b>Country:</b> {region}\n"
                f"✉️ <b>Message:</b> {escaped_body}\n\n"
                f"📡 <b>Source:</b> LIVE"
            )
            if u_id:
                body += f"\n\n👤 <b>User:</b> {u_name} (UID: <code>{u_id}</code>)"

            kb = [[rich_btn("📋 Copy OTP", style="primary", copy_text=otp_code)]]

            for gid in TARGET_GROUP_IDS:
                try: await send_rich_message(application.bot, gid, body, kb)
                except Exception as e: logger.error(f"Group forward failed: {e}")
            
            if u_id and not silent:
                inbox_msg = body + f"\n\n💰 <b>+Tk {payout:.2f} Added!</b>\n💳 <b>Total Balance: Tk {total_bal:.2f}</b>"
                try: await send_rich_message(application.bot, u_id, inbox_msg, kb)
                except Exception as e: logger.error(f"User inbox notify failed: {e}")

            return True
        except Exception as e:
            logger.error(f"Signal Routing Error: {e}")
            return False

def solve_math_captcha(text):
    match = re.search(r'(\d+)\s*([\+\-\*])\s*(\d+)', text)
    if match:
        num1, op, num2 = match.groups()
        num1, num2 = int(num1), int(num2)
        if op == '+': return str(num1 + num2)
        elif op == '-': return str(num1 - num2)
        elif op == '*': return str(num1 * num2)
    return ""

async def background_selenium_scraper(application):
    logger.info("Initializing Background Selenium WebDriver...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    driver = webdriver.Chrome(options=options)
    
    try:
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 10)
        
        username_field = wait.until(EC.presence_of_element_located((By.NAME, "username")))
        password_field = driver.find_element(By.NAME, "password")
        
        username_field.clear(); username_field.send_keys(USERNAME)
        password_field.clear(); password_field.send_keys(PASSWORD)
        
        page_text = driver.find_element(By.TAG_NAME, "body").text
        captcha_answer = solve_math_captcha(page_text)
        
        for inp in driver.find_elements(By.TAG_NAME, "input"):
            if inp.get_attribute("name") not in ["username", "password", None]:
                inp.clear(); inp.send_keys(captcha_answer)
                break
        
        driver.find_element(By.TAG_NAME, "button").click()
        await asyncio.sleep(3)
        driver.get(TARGET_URL)
        await asyncio.sleep(3)
        
        initial_messages = []
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        table = soup.find('table')
        if table:
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) >= 6:
                    date_time = cols[0].text.strip()
                    country_val = cols[1].text.strip()
                    number = cols[2].text.strip()
                    service_val = cols[3].text.strip()
                    sms_text = cols[5].text.strip()
                    
                    if not number or number == "0" or not sms_text or sms_text == "0": continue
                    
                    initial_messages.append({
                        'number': number, 'sms_text': sms_text,
                        'service_val': service_val, 'country_val': country_val if country_val else "General"
                    })
        
        for msg_data in reversed(initial_messages[:3]):
            n_clean = normalize_num(msg_data['number'])
            m_hash = hashlib.md5(f"{n_clean}_{msg_data['sms_text']}".encode()).hexdigest()
            processed_ids.add(m_hash)
            await engine_process_signal(application, msg_data, silent=True)
            await asyncio.sleep(0.5)

        logger.info("Selenium loop active. Monitoring new messages...")
        
        while True:
            try:
                if await get_config_val("api_status_Main", "1") == "0":
                    await asyncio.sleep(3.0)
                    continue

                driver.refresh()
                await asyncio.sleep(1.5)
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                table = soup.find('table')
                if table:
                    for row in table.find_all('tr')[1:]:
                        cols = row.find_all('td')
                        if len(cols) >= 6:
                            date_time = cols[0].text.strip()
                            country_val = cols[1].text.strip()
                            number = cols[2].text.strip()
                            service_val = cols[3].text.strip()
                            sms_text = cols[5].text.strip()
                            
                            if not number or number == "0" or not sms_text or sms_text == "0": continue
                            
                            n_clean = normalize_num(number)
                            m_hash = hashlib.md5(f"{n_clean}_{sms_text}".encode()).hexdigest()
                            
                            if m_hash not in processed_ids:
                                await engine_process_signal(application, {
                                    'number': number, 'sms_text': sms_text,
                                    'service_val': service_val, 'country_val': country_val if country_val else "General"
                                }, silent=False)
            except Exception as inner_e:
                logger.error(f"Scrape cycle error: {inner_e}")
            await asyncio.sleep(2.0)
    except Exception as e:
        logger.critical(f"Selenium critical failure: {e}")
    finally:
        driver.quit()

# =========================================================================
# --- HANDLERS & ROUTERS ---
# =========================================================================
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return
    uid, name = user.id, user.full_name
    if user.username: name = f"@{user.username}"
    
    if await check_is_restricted(uid):
        await update.message.reply_text("🚫 <b>Access Restricted!</b> You are banned.", parse_mode='HTML')
        return

    u_str = str(uid)
    if u_str not in CACHE_USERS:
        CACHE_USERS[u_str] = {'name': name, 'balance': 0.0, 'otp_count': 0, 'status': 'active', 'joined_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        save_db()

    if uid in ADMIN_IDS:
        await update.message.reply_text("🛠️ <b>Admin Panel Active</b>", reply_markup=build_admin_main(), parse_mode='HTML')
    else:
        await update.message.reply_text(f"👋 <b>Welcome {name}!</b>", reply_markup=build_user_main(), parse_mode='HTML')

async def router_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid, data = query.from_user.id, query.data
    if await check_is_restricted(uid): return

    if data == "select_country_menu":
        counts = {}
        for b_val in CACHE_STOCK.values():
            if isinstance(b_val, dict) and b_val.get('country'):
                c = b_val.get('country')
                cnt = len(b_val.get('numbers', []))
                if cnt > 0: counts[c] = counts.get(c, 0) + cnt
        if not counts: await query.answer("Stock empty.", show_alert=True)
        else:
            btns = [[rich_btn(f"{get_flag(c)} {c} ({cnt} numbers)", "primary", f"alloc_{c}")] for c, cnt in counts.items()]
            btns.append([rich_btn("🔙 Return", "danger", "exit_session")])
            await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, "🌍 <b>Select Region:</b>", btns)
            
    elif data.startswith("alloc_"):
        region = data.replace("alloc_", "")
        batch_size = context.user_data.get('p_count', 3)
        res, err = await engine_assign_batch(uid, region, batch_size)
        if err: 
            await query.answer(err, show_alert=True)
        else:
            msg_numbers = ""
            for n in res['numbers']:
                msg_numbers += f"📱 Number: <code>+{n['full']}</code>\n\n"
            
            kb = [[rich_btn("⏳ Waiting for OTP...", "primary", "none")]]
            no_code_btns = []
            for n in res['numbers']:
                btn = rich_btn(f"No Code: {n['short']}", style="primary", copy_text=n['short'])
                no_code_btns.append(btn)
            
            if batch_size == 10:
                for i in range(0, len(no_code_btns), 2):
                    kb.append(no_code_btns[i:i+2])
            else:
                for btn in no_code_btns:
                    kb.append([btn])
            
            kb.append([rich_btn("🔄 Refresh Stock", style="success", callback_data=f"alloc_{region}"), 
                       rich_btn("🔙 Back", style="danger", callback_data="select_country_menu")])
            
            text = f"<b>{res['header']}</b>\n📋 Click numbers to copy.\n\n{msg_numbers}"
            await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, text, kb)
            
    elif data == "exit_session":
        try: await query.message.delete()
        except: pass
        dash = build_admin_main() if uid in ADMIN_IDS else build_user_main()
        await context.bot.send_message(chat_id=uid, text="🎛️ Dashboard ready.", reply_markup=dash)

    elif data.startswith("adm_del_"):
        if uid not in ADMIN_IDS: return
        reg = data.replace("adm_del_", "")
        to_del = [b_id for b_id, b_val in CACHE_STOCK.items() if isinstance(b_val, dict) and b_val.get('country') == reg]
        for b_id in to_del: del CACHE_STOCK[b_id]
        save_db()
        await query.edit_message_text(f"🗑️ Stock cleared for {reg}")

    elif data == "adm_total_paid_show":
        if uid not in ADMIN_IDS: return
        limit_date = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            paid_users = set()
            total_sum = 0.0
            for w_id, w_val in CACHE_WITHDRAWALS.items():
                if isinstance(w_val, dict) and w_val.get('status') == 'accepted' and w_val.get('timestamp', '') >= limit_date:
                    paid_users.add(w_val.get('user_id'))
                    total_sum += float(w_val.get('amount', 0.0))
            text = f"📊 <b>Total Paid Stats (Last 6 Days)</b>\n\n👥 Total Users Paid: {len(paid_users)}\n💰 Total Paid Amount: Tk {total_sum:.2f}"
            await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, text, [[rich_btn("🔙 Back", style="primary", callback_data="exit_session")]])
        except Exception as e: logger.error(f"Stat failure: {e}")

    elif data == "adm_total_paid_clear":
        if uid not in ADMIN_IDS: return
        try:
            to_del = [w_id for w_id, w_val in CACHE_WITHDRAWALS.items() if isinstance(w_val, dict) and w_val.get('status') == 'accepted']
            for w_id in to_del: del CACHE_WITHDRAWALS[w_id]
            save_db()
            await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, "🗑️ Payout history successfully cleared.", [[rich_btn("🔙 Back", style="primary", callback_data="exit_session")]])
        except Exception as e: logger.error(f"Clear failure: {e}")

    elif data.startswith("w_method_"):
        channel = data.replace("w_method_", "")
        context.user_data['w_method'] = channel
        limit = float(await get_config_val('min_withdraw', '1000'))
        context.user_data['state'] = 'IN_W_AMT'
        await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, f"💳 You selected <b>{channel}</b>.\n💰 <b>Enter payout amount</b> (Minimum: Tk {limit}):", [])

    elif data == "back_stock_hist":
        await dispatch_stock_history_menu(update, context, edit_message_id=query.message.message_id)

    elif data.startswith("vstock_"):
        country = data.replace("vstock_", "")
        await display_country_stock(update, context, country)

    elif data.startswith("dlstock_"):
        country = data.replace("dlstock_", "")
        await download_country_stock_file(update, context, country)

    elif data.startswith("user_del_num_"):
        as_key = data.replace("user_del_num_", "")
        if as_key in CACHE_ASSIGNMENTS:
            as_val = CACHE_ASSIGNMENTS[as_key]
            if isinstance(as_val, dict) and as_val.get('user_id') == uid:
                country = as_val.get('country')
                del CACHE_ASSIGNMENTS[as_key]
                save_db()
                await display_country_stock(update, context, country)

    await query.answer()

async def router_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    raw = msg.text.strip()
    uid = msg.from_user.id
    name = msg.from_user.full_name
    if msg.from_user.username: name = f"@{msg.from_user.username}"

    state = context.user_data.get('state')
    if await check_is_restricted(uid): return

    if raw in ["📱 Get Number 3", "📱 Get Number 10"]:
        context.user_data['p_count'] = 3 if "3" in raw else 10
        await dispatch_country_ui(update, context)
        return
    elif raw == "💰 My Balance":
        u_val = CACHE_USERS.get(str(uid), {})
        u_bal = u_val.get('balance', 0.0) if isinstance(u_val, dict) else 0.0
        await msg.reply_text(f"💳 <b>Wallet Balance:</b> <code>Tk {float(u_bal):.2f}</code>", parse_mode='HTML')
        return
    elif raw == "📦 Stock History":
        await dispatch_stock_history_menu(update, context)
        return
    elif raw == "🏆 Leaderboard":
        await dispatch_leaderboard(msg)
        return
    elif raw == "💸 Withdraw Funds":
        kb = [[rich_btn("Bkash", style="primary", callback_data="w_method_Bkash"),
                rich_btn("Nagad", style="success", callback_data="w_method_Nagad"),
                rich_btn("Rocket", style="danger", callback_data="w_method_Rocket")]]
        await send_rich_message(context.bot, uid, "💳 <b>Select Payout Channel:</b>", kb)
        return

    if uid in ADMIN_IDS:
        if raw == "📥 Number Upload":
            await msg.reply_text("📥 <b>Inventory Hub:</b> Enter region label:"); context.user_data['state'] = 'ADM_UP_C'
            return
        elif raw == "🗑️ Clear Stock": 
            await dispatch_wipe_ui(update, context)
            return 
        elif raw == "👥 User List":
            await admin_export_user_db(update, context)
            return
        elif raw == "📊 Panel Stats": 
            await dispatch_panel_stats_inline(msg, context, edit=False)
            return
        elif raw == "💸 Withdraw Requests": 
            await dispatch_payout_ui(update, context)
            return
        elif raw == "💰 Total Paid":
            kb = [[rich_btn("Total Amount Paid", style="success", callback_data="adm_total_paid_show")],
                  [rich_btn("Clear Data Total Paid", style="danger", callback_data="adm_total_paid_clear")]]
            await send_rich_message(context.bot, uid, "📊 <b>Payout Statistics Terminal:</b>", kb)
            return
        elif raw == "📢 Broadcast":
            await msg.reply_text("📢 <b>Enter transmission payload:</b>"); context.user_data['state'] = 'ADM_BROAD'
            return
        elif raw == "🚫 Ban User":
            await msg.reply_text("🚫 <b>Enter target ID to ban:</b>"); context.user_data['state'] = 'ADM_BAN_U'
            return
        elif raw == "✅ Unban User":
            await msg.reply_text("✅ <b>Enter target ID to unban:</b>"); context.user_data['state'] = 'ADM_UNBAN_U'
            return
        elif raw == "⚙️ Set CC Limit":
            await dispatch_cc_limit_ui(update, context)
            return
        elif raw == "💰 Country Rate Set":
            await dispatch_country_rate_ui(update, context)
            return
        elif raw == "📦 Country Stock Limit":
            await dispatch_country_user_limit_ui(update, context)
            return

    if state == 'ADM_UP_C':
        context.user_data['temp_c'], context.user_data['state'] = raw, 'ADM_UP_F'
        await msg.reply_text(f"{get_flag(raw)} <b>Region set:</b> {raw}.\n📁 Now upload inventory .txt segment.", parse_mode='HTML')
        return
    elif state == 'ADM_BROAD':
        asyncio.create_task(run_background_broadcast(context, raw))
        await msg.reply_text("📢 Background broadcast transmission started.")
        context.user_data['state'] = None
        return
    elif state == 'ADM_BAN_U':
        try:
            if raw in CACHE_USERS and isinstance(CACHE_USERS[raw], dict):
                CACHE_USERS[raw]['status'] = 'banned'
                save_db()
            await msg.reply_text(f"🚫 User {raw} restricted successfully.")
        except Exception as e: await msg.reply_text(f"Failed: {e}")
        context.user_data['state'] = None
        return
    elif state == 'ADM_UNBAN_U':
        try:
            if raw in CACHE_USERS and isinstance(CACHE_USERS[raw], dict):
                CACHE_USERS[raw]['status'] = 'active'
                save_db()
            await msg.reply_text(f"✅ User {raw} restored successfully.")
        except Exception as e: await msg.reply_text(f"Failed: {e}")
        context.user_data['state'] = None
        return
    elif state == 'IN_W_AMT':
        try:
            amt = float(raw)
            limit = float(await get_config_val('min_withdraw', '1000'))
            if amt < limit:
                await msg.reply_text(f"⚠️ Amount is below minimum requirement of Tk {limit}.")
                context.user_data['state'] = None
                return
            u_val = CACHE_USERS.get(str(uid), {})
            bal = float(u_val.get('balance', 0.0)) if isinstance(u_val, dict) else 0.0
            if bal < amt:
                await msg.reply_text("❌ Insufficient funds.")
                context.user_data['state'] = None
                return
            context.user_data['temp_w_amt'] = amt
            context.user_data['state'] = 'IN_W_INFO'
            method = context.user_data.get('w_method', 'Bkash')
            await msg.reply_text(f"💳 <b>Enter your {method} Account Number:</b>", parse_mode='HTML')
        except ValueError: 
            await msg.reply_text("Numerical value required.")
            context.user_data['state'] = None
        return
    elif state == 'IN_W_INFO':
        amt = context.user_data['temp_w_amt']
        method = context.user_data.get('w_method', 'Bkash')
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        u_str = str(uid)
        u_val = CACHE_USERS.get(u_str, {})
        bal = float(u_val.get('balance', 0.0)) if isinstance(u_val, dict) else 0.0
        
        if bal >= amt:
            w_id = f"w_{int(datetime.now().timestamp())}_{uid}"
            CACHE_WITHDRAWALS[w_id] = {
                'user_id': uid, 'user_name': name, 'amount': amt,
                'info': raw, 'method': method, 'status': 'pending', 'timestamp': now_str
            }
            CACHE_USERS[u_str]['balance'] = bal - amt
            save_db()
            await msg.reply_text(f"✅ Your withdraw request of Tk {amt:.2f} via {method} has been submitted successfully.")
        else: await msg.reply_text("❌ Insufficient funds.")
        context.user_data['state'] = None
        return

async def handler_file_up(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS or context.user_data.get('state') != 'ADM_UP_F': return
    try:
        reg = context.user_data.get('temp_c', 'Unknown')
        doc = await update.message.document.get_file()
        path = f"tmp_{uid}.txt"
        await doc.download_to_drive(path)
        with open(path, "r", encoding='utf-8') as f:
            nums = [line.strip() for line in f if line.strip()]
        CACHE_STOCK[f"batch_{datetime.now().strftime('%Y%m%d%H%M%S')}"] = {"country": reg, "numbers": nums}
        save_db()
        await update.message.reply_text(f"✅ Success! Uploaded {len(nums)} numbers for {reg}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        if 'path' in locals() and os.path.exists(path): os.remove(path)
        context.user_data['state'] = None

async def dispatch_country_ui(update, context):
    counts = {}
    for b_val in CACHE_STOCK.values():
        if isinstance(b_val, dict) and b_val.get('country'):
            c = b_val.get('country')
            cnt = len(b_val.get('numbers', []))
            if cnt > 0: counts[c] = counts.get(c, 0) + cnt
    if counts:
        btns = [[rich_btn(f"{get_flag(c)} {c} ({cnt} numbers)", "primary", f"alloc_{c}")] for c, cnt in counts.items()]
        btns.append([rich_btn("🔙 Return", "danger", "exit_session")])
        await send_rich_message(context.bot, update.message.chat_id, "🌍 <b>Select Region Terminal:</b>", btns, parse_mode='HTML')
    else: await update.message.reply_text("⚠️ Inventory empty.")

async def dispatch_wipe_ui(update, context):
    countries = set(b_val.get('country') for b_val in CACHE_STOCK.values() if isinstance(b_val, dict) and b_val.get('country'))
    if countries:
        btns = [[rich_btn(f"Clear: {c}", "danger", f"adm_del_{c}")] for c in countries]
        await send_rich_message(context.bot, update.message.chat_id, "🗑️ Select country to clear:", btns)
    else: await update.message.reply_text("Stock empty.")

async def dispatch_panel_stats_inline(message, context, edit=False):
    status_val = await get_config_val("api_status_Main", "1")
    indicator = "🟢 ON" if status_val == "1" else "🔴 OFF"
    kb = [[rich_btn(f"Scraper Engine | {indicator}", "primary", "tog_api_Main")],
          [rich_btn("❌ Close Menu", "danger", "exit_session")]]
    text = "📊 <b>Panel Control Stats</b>\n\nLive background scraping status."
    if edit: await edit_rich_message(context.bot, message.chat_id, message.message_id, text, kb)
    else: await send_rich_message(context.bot, message.chat_id, text, kb)

async def dispatch_payout_ui(update, context):
    pending_found = False
    for w_id, w_val in CACHE_WITHDRAWALS.items():
        if isinstance(w_val, dict) and w_val.get('status') == 'pending':
            pending_found = True
            kb = [
                [rich_btn("Copy Info", "primary", copy_text=str(w_val.get('info')))],
                [rich_btn("Authorize", "success", f"adm_pay_acc_{w_id}"), 
                 rich_btn("Reject", "danger", f"adm_pay_rej_{w_id}")]
            ]
            msg_text = f"💸 <b>Withdraw Request</b>\nID: <code>{w_id}</code>\nUser: {w_val.get('user_name')}\nAmount: Tk {w_val.get('amount')}\nAcc: <code>{w_val.get('info')}</code>"
            await send_rich_message(context.bot, update.message.chat_id, msg_text, kb, parse_mode='HTML')
    if not pending_found: await update.message.reply_text("⚠️ Withdraw queue empty.")

async def dispatch_cc_limit_ui(update, context):
    countries = set(b_val.get('country') for b_val in CACHE_STOCK.values() if isinstance(b_val, dict) and b_val.get('country'))
    if countries:
        btns = [[rich_btn(f"CC Limit: {c}", "primary", f"adm_cc_{c}")] for c in countries]
        await send_rich_message(context.bot, update.message.chat_id, "⚙️ Select country:", btns)
    else: await update.message.reply_text("⚠️ No numbers found.")

async def dispatch_country_rate_ui(update, context):
    countries = set(b_val.get('country') for b_val in CACHE_STOCK.values() if isinstance(b_val, dict) and b_val.get('country'))
    if countries:
        btns = [[rich_btn(f"Rate: {c}", "primary", f"adm_rate_{c}")] for c in countries]
        await send_rich_message(context.bot, update.message.chat_id, "💰 Select country:", btns)
    else: await update.message.reply_text("⚠️ No countries found.")

async def dispatch_country_user_limit_ui(update, context):
    countries = set(b_val.get('country') for b_val in CACHE_STOCK.values() if isinstance(b_val, dict) and b_val.get('country'))
    if countries:
        btns = [[rich_btn(f"Limit: {c}", "primary", f"adm_ulimit_{c}")] for c in countries]
        await send_rich_message(context.bot, update.message.chat_id, "📦 Select country:", btns)
    else: await update.message.reply_text("⚠️ No countries found.")

async def admin_export_user_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stream = StringIO()
    stream.write("User List\n" + "="*20 + "\n")
    for u_id, u_val in CACHE_USERS.items():
        if isinstance(u_val, dict) and int(u_id) not in ADMIN_IDS:
            stream.write(f"ID: {u_id} | Name: {u_val.get('name')} | Bal: Tk {u_val.get('balance', 0)}\n")
    bio = BytesIO(stream.getvalue().encode('utf-8'))
    bio.name = "user_list.txt"
    await context.bot.send_document(chat_id=update.effective_chat.id, document=bio, caption="👥 User List")

async def run_background_broadcast(context, payload):
    for u_id in list(CACHE_USERS.keys()):
        try: 
            await send_rich_message(context.bot, int(u_id), f'📢 <b>Official Notice:</b>\n\n{payload}', [])
            await asyncio.sleep(0.035) 
        except: pass

async def dispatch_stock_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message_id=None):
    uid = update.callback_query.from_user.id if update.callback_query else update.effective_user.id
    chat_id = update.callback_query.message.chat_id if update.callback_query else update.message.chat_id
        
    counts = {}
    for as_key, as_val in CACHE_ASSIGNMENTS.items():
        if isinstance(as_val, dict) and as_val.get('user_id') == uid and as_val.get('status') == 'assigned':
            country = as_val.get('country', 'Global')
            counts[country] = counts.get(country, 0) + 1

    if not counts:
        text = "📦 <b>Your Active Stock History:</b>\n\n⚠️ You do not have any active numbers in stock right now."
        kb = [[rich_btn("❌ Close Menu", style="danger", callback_data="exit_session")]]
    else:
        text = "📦 <b>Your Active Stock History:</b>\n\nSelect a country below to view, manage, or export your active stocked numbers:"
        kb = []
        for country, count in counts.items():
            kb.append([rich_btn(f"{get_flag(country)} {country} ({count} numbers)", style="primary", callback_data=f"vstock_{country}")])
        kb.append([rich_btn("❌ Close Menu", style="danger", callback_data="exit_session")])
        
    if edit_message_id:
        await edit_rich_message(context.bot, chat_id, edit_message_id, text, kb)
    else:
        await send_rich_message(context.bot, uid, text, kb)

async def display_country_stock(update: Update, context: ContextTypes.DEFAULT_TYPE, country: str):
    query = update.callback_query
    uid = query.from_user.id
    
    assigned_items = []
    for as_key, as_val in CACHE_ASSIGNMENTS.items():
        if isinstance(as_val, dict) and as_val.get('user_id') == uid and as_val.get('country') == country and as_val.get('status') == 'assigned':
            assigned_items.append((as_key, as_val.get('number')))

    if not assigned_items:
        text = f"⚠️ No active stock found for {get_flag(country)} <b>{country}</b>."
        kb = [[rich_btn("🔙 Back to Stock History", style="danger", callback_data="back_stock_hist")]]
        await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, text, kb)
        return
        
    count = len(assigned_items)
    display_text = f"📦 <b>Active stocked numbers for {get_flag(country)} {country} ({count} nos):</b>\n\nClick any number below to delete it from your stock if unwanted:\n\n"
    
    kb = []
    for as_key, num in assigned_items[:25]:
        clean = normalize_num(num)
        kb.append([
            rich_btn(f"+{clean}", style="primary", copy_text=clean),
            rich_btn("🗑️ Delete", style="danger", callback_data=f"user_del_num_{as_key}")
        ])
        
    if count > 25:
        display_text += f"\n<i>...showing first 25 numbers.</i>\n"
        
    kb.append([rich_btn("📥 Download .txt List", style="success", callback_data=f"dlstock_{country}")])
    kb.append([rich_btn("🔙 Back to Stock History", style="primary", callback_data="back_stock_hist")])
    
    await edit_rich_message(context.bot, query.message.chat_id, query.message.message_id, display_text, kb)

async def download_country_stock_file(update: Update, context: ContextTypes.DEFAULT_TYPE, country: str):
    query = update.callback_query
    uid = query.from_user.id
    
    numbers_list = []
    for as_key, as_val in CACHE_ASSIGNMENTS.items():
        if isinstance(as_val, dict) and as_val.get('user_id') == uid and as_val.get('country') == country and as_val.get('status') == 'assigned':
            numbers_list.append(as_val.get('number'))

    if not numbers_list:
        await query.answer("⚠️ No active stock found to export.", show_alert=True)
        return
        
    stream = StringIO()
    stream.write(f"Active Stock list for country: {country}\n")
    stream.write(f"Total synced: {len(numbers_list)}\n")
    stream.write("="*40 + "\n\n")
    for num in numbers_list:
        clean = normalize_num(num)
        stream.write(f"+{clean}\n")
        
    bio = BytesIO(stream.getvalue().encode('utf-8'))
    bio.name = f"{country}_active_stock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        await context.bot.send_document(chat_id=uid, document=bio, caption=f"📥 Complete list of active numbers for {get_flag(country)} <b>{country}</b>.")
    except Exception as e:
        logger.error(f"Failed to transmit file: {e}")

async def dispatch_leaderboard(message):
    midnight_str = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
    
    scores = []
    for u_id, p_msgs in CACHE_PROCESSED.items():
        if isinstance(p_msgs, dict):
            count = 0
            for m_hash, m_data in p_msgs.items():
                if isinstance(m_data, dict) and m_data.get('timestamp', '') >= midnight_str:
                    count += 1
            if count > 0:
                u_val = CACHE_USERS.get(str(u_id), {})
                u_name = u_val.get('name', 'User') if isinstance(u_val, dict) else 'User'
                scores.append((u_name, count))
            
    scores.sort(key=lambda x: x[1], reverse=True)
    
    if scores:
        out = f"🏆 <b>Daily Ranking (Since 12 AM Today):</b>\n\n"
        for i, (name, score) in enumerate(scores[:10], 1):
            cl_name = name[:-3] if len(name) > 3 else "User"
            out += f"{i}. {cl_name}... - OTP Success: {score}\n"
        await message.reply_text(out, parse_mode='HTML')
    else: 
        await message.reply_text("⚠️ Terminal Data: No activity yet today.")

async def check_is_restricted(uid):
    u_val = CACHE_USERS.get(str(uid), {})
    return u_val.get('status') == 'banned' if isinstance(u_val, dict) else False

async def app_post_init(app):
    asyncio.create_task(background_selenium_scraper(app))

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception occurred during live update cycle:", exc_info=context.error)

if __name__ == '__main__':
    try:
        instance = ApplicationBuilder().token(TOKEN).post_init(app_post_init).build()
        instance.add_handler(CommandHandler("start", handle_start))
        instance.add_handler(CallbackQueryHandler(router_callbacks))
        instance.add_handler(MessageHandler(filters.Document.ALL, handler_file_up))
        instance.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), router_text))
        instance.add_error_handler(error_handler)
        
        logger.info("Bot started successfully with complete bug fixes.")
        instance.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.critical(f"Panic Shutdown: {e}")

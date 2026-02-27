import pandas as pd
import datetime as dt
import talib
import time
import requests
import pyotp
import warnings
import os
import threading
import calendar
from flask import Flask
from SmartApi import SmartConnect

warnings.filterwarnings("ignore")

# ================= CONFIG (API KEYS HARDCODED AS REQUESTED) =================
# ⚠️ WARNING: Never share this file publicly! ⚠️
API_KEY        = "yRe368gf"
CLIENT_ID      = "AABZ146183"
PASSWORD       = "6211"
TOTP_SECRET    = "ZHFAFO7SKLYN3FNJOBPZYNEGQI"
TELEGRAM_TOKEN = "8291109950:AAE-vcehleqwpl0Bc-2o1dlaUOEQNWw9r-4"
CHAT_ID        = "1901759813"

TRADE_QTY              = 25       # 1 Lot fixed
LOT_SIZE               = 25
MAX_DAILY_LOSS         = 1200     # Adjusted for 10k Capital
PAPER_TRADE            = True     # Live ke liye False karo
LOG_FILE               = "bot_logs.txt"
SESSION_REFRESH_HOURS  = 6
INSTRUMENT_REFRESH_MIN = 60
# ============================================================================

# ╔══════════════════════════════════════════════════════════════════╗
# ║    TIME FILTER CONFIG (MODULE v3)                                ║
# ╚══════════════════════════════════════════════════════════════════╝
TRADING_START      = dt.time(9, 25)    
NORMAL_MORNING_END = dt.time(11, 30)   
MIDDAY_BLOCK_END   = dt.time(13, 30)   
TRADING_HARD_STOP  = dt.time(14, 45)   

INDEX_CONFIG = {
    "NIFTY": {
        "weekly_expiry_weekday": 1,  # 👈 100% CORRECT: Tuesday is Expiry
        "expiry_cutoff_time":    dt.time(11, 0),
    }
}

STRICT_MONTHLY_EXPIRY         = True
MONTHLY_EXPIRY_REDUCTION_MIN  = 30   

def get_last_expiry_weekday_of_month(year, month, target_weekday):
    if month == 12:
        last_day = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        last_day = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    days_back = (last_day.weekday() - target_weekday) % 7
    return last_day - dt.timedelta(days=days_back)

def is_monthly_expiry_day(today, index_name):
    config         = INDEX_CONFIG.get(index_name, {})
    expiry_weekday = config.get("weekly_expiry_weekday")
    if expiry_weekday is None: return False
    last_expiry = get_last_expiry_weekday_of_month(today.year, today.month, expiry_weekday)
    return today == last_expiry

def get_effective_expiry_cutoff(today, index_name):
    config      = INDEX_CONFIG.get(index_name, {})
    base_cutoff = config.get("expiry_cutoff_time", dt.time(11, 0))
    if STRICT_MONTHLY_EXPIRY and is_monthly_expiry_day(today, index_name):
        dummy_dt   = dt.datetime.combine(today, base_cutoff)
        strict_dt  = dummy_dt - dt.timedelta(minutes=MONTHLY_EXPIRY_REDUCTION_MIN)
        return strict_dt.time()
    return base_cutoff

def is_expiry_day(today, index_name):
    config         = INDEX_CONFIG.get(index_name, {})
    expiry_weekday = config.get("weekly_expiry_weekday")
    if expiry_weekday is None: return False
    return today.weekday() == expiry_weekday

def is_in_trading_window(now, index_name="NIFTY"):
    t     = now.time()
    today = now.date()

    if now.weekday() >= 5:
        return False, "Weekend — market closed"
    if t < TRADING_START:
        return False, f"Pre-market — entries open at {TRADING_START}"
    if t >= TRADING_HARD_STOP:
        return False, f"Hard stop — no new entries after {TRADING_HARD_STOP}"

    if is_expiry_day(today, index_name):
        effective_cutoff = get_effective_expiry_cutoff(today, index_name)
        expiry_weekday   = INDEX_CONFIG[index_name]["weekly_expiry_weekday"]
        day_name         = calendar.day_name[expiry_weekday]

        if t >= effective_cutoff:
            if STRICT_MONTHLY_EXPIRY and is_monthly_expiry_day(today, index_name):
                return False, f"⛔ {index_name} monthly expiry restriction active (strict cutoff {effective_cutoff})."
            return False, f"⛔ {index_name} {day_name} expiry restriction active (post {effective_cutoff})."
        return True, f"{index_name} expiry window open ({TRADING_START}–{effective_cutoff})"

    if t < NORMAL_MORNING_END:
        return True, f"Morning session ({TRADING_START}–{NORMAL_MORNING_END})"
    if NORMAL_MORNING_END <= t < MIDDAY_BLOCK_END:
        return False, f"Midday block ({NORMAL_MORNING_END}–{MIDDAY_BLOCK_END})"
    return False, f"Post-midday — system is morning-session only (hard stop at {TRADING_HARD_STOP})"

# ======================== CORE FUNCTIONS ========================
def custom_print(msg, send_tg=False):
    time_str = dt.datetime.now().strftime('%H:%M:%S')
    full_msg  = f"[{time_str}] {msg}"
    print(full_msg, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(full_msg + "<br>\n")
    except Exception: pass
    if send_tg:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": f"📢 {msg}"}, timeout=5)
        except Exception as e:
            print(f"[{time_str}] ⚠️ Telegram Error: {e}", flush=True)

def create_session():
    for attempt in range(3):
        try:
            custom_print(f"🔐 SmartAPI Session Bana Raha Hoon (Attempt {attempt + 1})...")
            obj  = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
            if data and data.get("status"):
                custom_print("✅ Session Ban Gaya!")
                return obj
            else:
                custom_print(f"⚠️ Session Response: {data}")
        except Exception as e:
            custom_print(f"⚠️ Session Error (Attempt {attempt + 1}): {e}")
        time.sleep(5)
    custom_print("❌ FATAL: 3 baar try karne ke baad bhi session nahi bana.", send_tg=True)
    return None

def get_instrument_master():
    url     = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(3):
        try:
            custom_print(f"📥 Instrument Master Download Ho Raha Hai (Attempt {attempt + 1})...")
            res = requests.get(url, headers=headers, timeout=30)
            if res.status_code == 200:
                df            = pd.DataFrame(res.json())
                df['expiry']  = pd.to_datetime(df['expiry'], errors='coerce')
                df['strike']  = pd.to_numeric(df['strike'], errors='coerce')
                custom_print(f"✅ Instrument Master Ready! Total Symbols: {len(df)}")
                return df
        except Exception as e:
            custom_print(f"⚠️ Network Error: {e}")
        time.sleep(5)
    custom_print("❌ FATAL: Instrument Master nahi aaya.", send_tg=True)
    return None

def get_nifty_spot_token(df_master):
    try:
        df_nifty = df_master[(df_master['name'] == 'Nifty 50') & (df_master['instrumenttype'] == 'AMXIDX') & (df_master['exch_seg'] == 'NSE')]
        if not df_nifty.empty: return str(df_nifty.iloc[0]['token'])
        df_nifty2 = df_master[(df_master['symbol'] == 'Nifty 50') & (df_master['exch_seg'] == 'NSE')]
        if not df_nifty2.empty: return str(df_nifty2.iloc[0]['token'])
    except Exception as e:
        custom_print(f"⚠️ get_nifty_spot_token error: {e}")
    return None

def get_ohlc_data(obj, token, interval, days=5):
    try:
        now = dt.datetime.now()
        res = obj.getCandleData({
            "exchange": "NSE", "symboltoken": token, "interval": interval,
            "fromdate": (now - dt.timedelta(days=days)).strftime("%Y-%m-%d 09:15"),
            "todate":   now.strftime("%Y-%m-%d %H:%M")
        })
        if res and res.get("data") and len(res["data"]) > 0:
            df = pd.DataFrame(res["data"], columns=['date', 'o', 'h', 'l', 'c', 'v'])
            df[['o', 'h', 'l', 'c', 'v']] = df[['o', 'h', 'l', 'c', 'v']].astype(float)
            return df
    except Exception as e:
        custom_print(f"⚠️ get_ohlc_data error: {e}")
    return None

def get_atm_option(df_master, spot_price, opt_type):
    try:
        strike     = round(spot_price / 50) * 50
        strike_val = float(strike * 100)
        df = df_master[(df_master['name'] == 'NIFTY') & (df_master['instrumenttype'] == 'OPTIDX') & 
                       (df_master['symbol'].str.endswith(opt_type)) & (df_master['strike'] == strike_val)].copy()
        df = df[df['expiry'] >= dt.datetime.now()].sort_values(by='expiry')
        if not df.empty: return df.iloc[0]['token'], df.iloc[0]['symbol']
    except Exception as e:
        custom_print(f"⚠️ get_atm_option error: {e}")
    return None, None

def place_order(obj, symbol, token, transaction_type, qty):
    if PAPER_TRADE:
        custom_print(f"📝 [PAPER TRADE] {transaction_type} | {symbol} | Qty: {qty}")
        return True
    try:
        res = obj.placeOrder({
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
            "transactiontype": transaction_type, "exchange": "NFO", "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": str(qty)
        })
        custom_print(f"✅ Order Placed: {transaction_type} {qty} {symbol} | Res: {res}")
        return True
    except Exception as e:
        custom_print(f"❌ Order Error: {e}", send_tg=True)
        return False

# ======================== MAIN TRADING LOOP ========================
session_start_time = dt.datetime.now()

def inner_trading_loop(obj, df_master_ref):
    global session_start_time
    trade_active           = False
    trade                  = {}
    daily_pnl              = 0
    last_inst_refresh      = dt.datetime.now()
    last_time_block_reason = None
    entry_window_printed   = False

    nifty_token = get_nifty_spot_token(df_master_ref['df'])
    if nifty_token is None:
        custom_print("❌ NIFTY token nahi mila. Loop band ho raha hai.")
        return "STOP"

    custom_print(f"💎 PRO-BOT ACTIVE | NIFTY Token: {nifty_token} | Qty: {TRADE_QTY}", send_tg=True)

    while True:
        now = dt.datetime.now()

        # ── TIME FILTER INTEGRATION (v3) ──────────────────────
        in_window, window_reason = is_in_trading_window(now, index_name="NIFTY")

        if in_window and not entry_window_printed:
            custom_print("🟢 Entry Window Active")
            entry_window_printed = True
        elif not in_window:
            entry_window_printed = False

        if not in_window:
            if not trade_active:
                if window_reason != last_time_block_reason:
                    custom_print(f"⏰ {window_reason}")
                    last_time_block_reason = window_reason
                time.sleep(60)
                continue
        else:
            if last_time_block_reason is not None and not trade_active:
                custom_print(f"✅ Trading window open: {window_reason}")
                last_time_block_reason = None
        # ───────────────────────────────────────────────────────

        if daily_pnl <= -MAX_DAILY_LOSS:
            custom_print(f"🛑 Max Daily Loss Rs{MAX_DAILY_LOSS} Hit Ho Gaya! Aaj ke liye trading band.", send_tg=True)
            return "STOP"

        mins_since = (now - last_inst_refresh).total_seconds() / 60
        if mins_since >= INSTRUMENT_REFRESH_MIN:
            custom_print("🔄 Instrument Master Refresh Ho Raha Hai...")
            new_master = get_instrument_master()
            if new_master is not None:
                df_master_ref['df'] = new_master
                last_inst_refresh   = now
                new_token = get_nifty_spot_token(df_master_ref['df'])
                if new_token: nifty_token = new_token
            else:
                custom_print("⚠️ Instrument Master refresh fail, purana use ho raha hai.")

        df_master = df_master_ref['df']

        try:
            df_15 = get_ohlc_data(obj, nifty_token, "FIFTEEN_MINUTE")
            df_5  = get_ohlc_data(obj, nifty_token, "FIVE_MINUTE")

            if df_15 is None or len(df_15) < 201:
                custom_print("❌ 15m candles insufficient or fetch failed")
                time.sleep(15)
                continue
            if df_5 is None or len(df_5) < 15:
                custom_print("❌ 5m candles insufficient or fetch failed")
                time.sleep(15)
                continue

            ema_200_15   = talib.EMA(df_15['c'], 200).iloc[-1]
            current_spot = df_15['c'].iloc[-1]
            rsi_5        = talib.RSI(df_5['c'], 14).iloc[-1]
            trend        = "BULL 🟢" if current_spot > ema_200_15 else "BEAR 🔴"

            # DEBUG HEARTBEAT
            custom_print(f"💓 HEARTBEAT | 15m count: {len(df_15)} | 5m count: {len(df_5)} | Spot: {current_spot:.1f} | EMA200: {ema_200_15:.1f} | RSI: {rsi_5:.1f} | Trend: {trend} | trade_active: {trade_active}")

            # ----- EXIT LOGIC (Prioritized to ensure independent execution) -----
            if trade_active:
                ltp_res = obj.ltpData("NFO", trade['symbol'], trade['token'])
                if not ltp_res or not ltp_res.get("data"): 
                    custom_print("❌ LTP fetch failed for option")
                    time.sleep(15)
                    continue

                ltp = float(ltp_res["data"]["ltp"])
                
                if ltp > trade['entry'] * 1.10 and trade['sl'] < trade['entry']:
                    trade['sl'] = trade['entry']
                    custom_print(f"🛡️ SL Breakeven Pe: {trade['entry']} | {trade['symbol']}", send_tg=True)

                reason = None
                if ltp <= trade['sl']: reason = "StopLoss Hit 🛑"
                elif ltp >= trade['target']: reason = "Target Hit 🎯"
                elif now.time() >= dt.time(15, 10): reason = "EOD Square-off 🕒"

                if reason:
                    place_order(obj, trade['symbol'], trade['token'], "SELL", trade['qty'])
                    pnl       = (ltp - trade['entry']) * trade['qty']
                    daily_pnl += pnl
                    custom_print(f"🏁 EXIT: {reason} | PnL: Rs{round(pnl, 2)} | Daily: Rs{round(daily_pnl, 2)}", send_tg=True)
                    trade_active = False
                    trade        = {}

            # ----- ENTRY LOGIC -----
            elif not trade_active and in_window:
                direction = None
                if "BULL" in trend and rsi_5 > 65: direction = "CE"
                elif "BEAR" in trend and rsi_5 < 35: direction = "PE"

                if direction:
                    token, symbol = get_atm_option(df_master, current_spot, direction)
                    if not token: 
                        custom_print("❌ ATM option not found for strike")
                        time.sleep(30)
                        continue

                    ltp_res = obj.ltpData("NFO", symbol, token)
                    if not ltp_res or not ltp_res.get("data"): 
                        custom_print("❌ LTP fetch failed for option")
                        time.sleep(15)
                        continue

                    opt_ltp   = float(ltp_res["data"]["ltp"])
                    sl_points = opt_ltp * 0.15
                    qty       = TRADE_QTY

                    success = place_order(obj, symbol, token, "BUY", qty)
                    if success:
                        trade = {
                            "symbol": symbol, "token": token, "entry": opt_ltp, "qty": qty,
                            "sl": round(opt_ltp - sl_points, 2), "target": round(opt_ltp + (sl_points * 2), 2)
                        }
                        trade_active = True
                        custom_print(f"🚀 ENTRY: {symbol} | Price: {opt_ltp} | Qty: {qty} | SL: {trade['sl']}", send_tg=True)
                else:
                    custom_print("⏭ Entry skipped — Conditions not met")

        except Exception as e:
            custom_print(f"⚠️ Loop Error: {e}")
            time.sleep(15)
            continue

        time.sleep(30)
        hours_since = (dt.datetime.now() - session_start_time).total_seconds() / 3600
        if hours_since >= SESSION_REFRESH_HOURS:
            custom_print("🔄 Session refresh time aa gaya...")
            return "REFRESH_SESSION"

    return "STOP"

# ======================== OUTER ENGINE ========================
def run_pro_engine():
    global session_start_time
    if os.path.exists(LOG_FILE): os.remove(LOG_FILE)

    custom_print("=" * 55)
    custom_print("🚀 ALGO TRADING BOT SHURU HO RAHA HAI...")
    custom_print(f"   Paper Mode: {PAPER_TRADE} | Qty: {TRADE_QTY} | Max Loss: Rs{MAX_DAILY_LOSS}")
    custom_print("=" * 55)

    df_master = get_instrument_master()
    if df_master is None:
        custom_print("❌ Instrument Master ke bina bot nahi chal sakta.")
        return

    df_master_ref = {'df': df_master}

    while True:
        session_start_time = dt.datetime.now()
        obj = create_session()

        if obj is None:
            time.sleep(60)
            continue

        result = inner_trading_loop(obj, df_master_ref)

        if result == "REFRESH_SESSION":
            custom_print("♻️ Session refresh ho raha hai...")
            time.sleep(2)
            continue
        elif result == "STOP":
            now      = dt.datetime.now()
            next_day = (now + dt.timedelta(days=1)).replace(hour=9, minute=10, second=0)
            sleep_sec = (next_day - now).total_seconds()
            custom_print(f"💤 Aaj trading band. Next start: {next_day.strftime('%d-%m-%Y %H:%M')} ({int(sleep_sec/3600)} ghante baad)")
            time.sleep(max(sleep_sec, 3600))
            continue

# ======================== FLASK WEB SERVER ========================
app = Flask(__name__)

@app.route('/')
def home():
    mode = "PAPER MODE" if PAPER_TRADE else "⚠️ LIVE MODE"
    return f"<h1>✅ Algo Bot Running | {mode}</h1><p><a href='/logs'>📋 Live Logs Dekho</a></p><p>Qty: {TRADE_QTY} | Max Daily Loss: Rs{MAX_DAILY_LOSS}</p>"

@app.route('/logs')
def show_logs():
    try:
        with open(LOG_FILE, "r") as f:
            lines   = f.readlines()[-150:]
            content = "".join(lines)
    except Exception:
        content = "Bot abhi logs generate nahi kar raha..."

    mode_color = "yellow" if PAPER_TRADE else "red"
    mode_text  = "PAPER" if PAPER_TRADE else "⚠️ LIVE"

    html = f"""
    <html>
        <head>
            <title>Bot Logs</title>
            <meta http-equiv="refresh" content="30">
        </head>
        <body style='background:black; color:lime; font-family:monospace; padding:20px;'>
            <h2>🚀 LIVE TRADING TERMINAL</h2>
            <p style='color:gray;'>Auto-refresh: 30s | Mode: <b style='color:{mode_color}'>{mode_text}</b> | Qty: <b>{TRADE_QTY}</b></p>
            <hr style='border-color:lime;'>
            {content}
        </body>
    </html>
    """
    return html

def start_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

if __name__ == "__main__":
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    custom_print(f"🌐 Web server port {os.environ.get('PORT', 10000)} pe chalu hua")
    run_pro_engine()

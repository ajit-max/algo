import os
import threading
import pandas as pd
import datetime as dt
import talib
import time
import requests
from SmartApi import SmartConnect
import pyotp
import warnings
from flask import Flask
from collections import deque

# Flask App setup
app = Flask(__name__)
warnings.filterwarnings("ignore")

# Latest 100 lines of logs store karne ke liye (Website ke liye)
logs_storage = deque(maxlen=100)

def log_and_print(msg):
    """Print bhi karega aur website logs mein bhi daalega"""
    timestamp = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg, flush=True)
    logs_storage.appendleft(formatted_msg) # New logs at top

# ================= CONFIG (Environment Variables) =================
API_KEY = os.getenv("API_KEY", "yRe368gf")
CLIENT_ID = os.getenv("CLIENT_ID", "AABZ146183")
PASSWORD = os.getenv("PASSWORD", "6211")
TOTP_SECRET = os.getenv("TOTP_SECRET", "ZHFAFO7SKLYN3FNJOBPZYNEGQI")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8291109950:AAE-vcehleqwpl0Bc-2o1dlaUOEQNWw9r-4")
CHAT_ID = os.getenv("CHAT_ID", "1901759813")
INDEX_TOKEN = "99926000"  # NIFTY 50
# =====================================================================

@app.route('/')
def home():
    return "<h3>Bot is Running!</h3><p>To see live activity, go to: <a href='/logs'>/logs</a></p>"

@app.route('/logs')
def show_logs():
    """Website par logs dikhane ke liye hacker-style interface"""
    html = "<html><head><title>Trading Bot Logs</title>"
    html += "<meta http-equiv='refresh' content='30'>" # Auto-refresh every 30s
    html += "<style>body{background:#0d1117; color:#58a6ff; font-family:monospace; padding:20px;} .ce{color:#238636;} .pe{color:#da3633;} .warn{color:#d29922;}</style></head><body>"
    html += "<h2>🚀 Live Trading Activity</h2><hr style='border:0.5px solid #30363d;'>"
    
    if not logs_storage:
        html += "<p>Waiting for market session or first scan...</p>"
    else:
        for log in logs_storage:
            color_class = ""
            if "CE" in log: color_class = "class='ce'"
            elif "PE" in log: color_class = "class='pe'"
            elif "⚠️" in log or "❌" in log: color_class = "class='warn'"
            html += f"<div {color_class}>{log}</div>"
            
    html += "</body></html>"
    return html

def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={msg}&parse_mode=Markdown"
    try:
        requests.get(url, timeout=10)
    except:
        log_and_print("❌ Telegram Alert Failed!")

def get_strike(price, opt_type):
    base = round(price / 50) * 50
    return (base - 50) if opt_type == "CE" else (base + 50)

def connect_angel():
    try:
        obj = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        obj.generateSession(CLIENT_ID, PASSWORD, totp)
        log_and_print("✅ Angel One Connected Successfully!")
        return obj
    except Exception as e:
        log_and_print(f"❌ Connection Error: {e}")
        return None

def live_scanner_logic():
    log_and_print("📡 Scanner Background Thread Started...")
    obj = None
    trade_active = False
    current_trade = {}

    while True:
        try:
            now = dt.datetime.now()
            
            # Market Time Check (9:15 AM to 3:30 PM)
            if dt.time(9,15) <= now.time() <= dt.time(15,30):
                if obj is None:
                    obj = connect_angel()
                    if obj: send_telegram_msg("🔄 *Bot Logged In* - Scanner Active")
                    else: 
                        time.sleep(60)
                        continue

                # Data Fetching
                from_date = (now - dt.timedelta(days=7)).strftime("%Y-%m-%d 09:15")
                to_date = now.strftime("%Y-%m-%d %H:%M")
                
                res = obj.getCandleData({
                    "exchange": "NSE", "symboltoken": INDEX_TOKEN,
                    "interval": "FIVE_MINUTE", "fromdate": from_date, "todate": to_date
                })
                
                if res['status'] and res['data']:
                    df = pd.DataFrame(res['data'], columns=['date','open','high','low','close','volume'])
                    df[['high','low','close']] = df[['high','low','close']].astype(float)
                    
                    if len(df) >= 201:
                        curr_price = df['close'].iloc[-1]
                        ema200 = talib.EMA(df['close'], 200).iloc[-1]
                        rsi = talib.RSI(df['close'], 14).iloc[-1]
                        atr = talib.ATR(df['high'], df['low'], df['close'], 14).iloc[-1]

                        log_and_print(f"🔍 Price: {curr_price} | RSI: {round(rsi,2)} | EMA200: {round(ema200,2)}")

                        if not trade_active:
                            signal_type = ""
                            if curr_price > ema200 and 60 < rsi < 75:
                                signal_type = "CE"
                            elif curr_price < ema200 and 25 < rsi < 40:
                                signal_type = "PE"

                            if signal_type != "":
                                strike = get_strike(curr_price, signal_type)
                                sl = round(curr_price - (1.2 * atr), 1) if signal_type=="CE" else round(curr_price + (1.2 * atr), 1)
                                tg = round(curr_price + (2.5 * atr), 1) if signal_type=="CE" else round(curr_price - (2.5 * atr), 1)
                                
                                current_trade = {"type": signal_type, "entry": curr_price, "strike": strike, "sl": sl, "tg": tg}
                                msg = (f"🚀 *ENTRY SIGNAL: NIFTY {signal_type}*\n"
                                       f"📍 Entry: {curr_price} | Strike: {strike}\n"
                                       f"🛑 SL: {sl} | 🏁 TG: {tg}")
                                send_telegram_msg(msg)
                                log_and_print(f"✅ ENTRY ALERT SENT: {signal_type}")
                                trade_active = True
                        else:
                            # Monitoring for Exit
                            exit_triggered = False
                            reason = ""
                            if current_trade["type"] == "CE":
                                if curr_price <= current_trade["sl"]: exit_triggered, reason = True, "StopLoss Hit 🛑"
                                elif curr_price >= current_trade["tg"]: exit_triggered, reason = True, "Target Hit 🏁"
                            else: # PE
                                if curr_price >= current_trade["sl"]: exit_triggered, reason = True, "StopLoss Hit 🛑"
                                elif curr_price <= current_trade["tg"]: exit_triggered, reason = True, "Target Hit 🏁"
                            
                            if now.time() >= dt.time(15, 10): exit_triggered, reason = True, "EOD Exit 🕒"

                            if exit_triggered:
                                pnl = round(curr_price - current_trade["entry"], 2) if current_trade["type"]=="CE" else round(current_trade["entry"] - curr_price, 2)
                                send_telegram_msg(f"🔔 *EXIT: {reason}*\nPrice: {curr_price} | PnL: {pnl}")
                                log_and_print(f"❌ EXIT ALERT SENT: {reason}")
                                trade_active = False
                
                elif "TooManyRequests" in str(res.get('message', '')):
                    log_and_print("🛑 API Rate Limit! Resting 70s...")
                    time.sleep(70)

            else:
                if obj is not None: obj = None # Reset session daily
                log_and_print("💤 Market Closed. Waiting for 9:15 AM...")
                time.sleep(300)

        except Exception as e:
            log_and_print(f"⚠️ Loop Error: {e}")
            time.sleep(15)
        
        time.sleep(60)

if __name__ == "__main__":
    t = threading.Thread(target=live_scanner_logic)
    t.daemon = True
    t.start()
    
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

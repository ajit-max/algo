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

# Flask App setup
app = Flask(__name__)
warnings.filterwarnings("ignore")

# ================= CONFIG (Environment Variables) =================
# Render ke dashboard mein ye sab keys zaroor daal dena
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
    # Ye page Uptime Robot ko "Live" status dikhayega
    now = dt.datetime.now().strftime('%H:%M:%S')
    return f"Bot is running. Server Time: {now} (IST if TZ is set)"

def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={msg}&parse_mode=Markdown"
    try:
        requests.get(url, timeout=10)
    except:
        print("❌ Telegram Alert Failed!")

def get_strike(price, opt_type):
    base = round(price / 50) * 50
    return (base - 50) if opt_type == "CE" else (base + 50)

def connect_angel():
    try:
        obj = SmartConnect(api_key=API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET).now()
        res = obj.generateSession(CLIENT_ID, PASSWORD, totp)
        if res['status']:
            print("✅ Angel One Connected!")
            return obj
        return None
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None

def live_scanner_logic():
    obj = None
    trade_active = False
    current_trade = {}
    
    print("📡 Scanner Background Thread Started...")

    while True:
        try:
            now = dt.datetime.now()
            
            # 1. Market Time Check (9:15 AM to 3:30 PM)
            if dt.time(9,15) <= now.time() <= dt.time(15,35):
                
                # Agar market open hai aur session nahi hai, toh login karo
                if obj is None:
                    obj = connect_angel()
                    if obj:
                        send_telegram_msg("🔄 *Bot Logged In* - Monitoring Started")
                    else:
                        time.sleep(60) # Login fail ho toh wait karo
                        continue

                # Data fetching
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

                        # --- ENTRY ---
                        if not trade_active:
                            signal = ""
                            if curr_price > ema200 and 60 < rsi < 75:
                                signal = "CE"
                            elif curr_price < ema200 and 25 < rsi < 40:
                                signal = "PE"

                            if signal != "":
                                strike = get_strike(curr_price, signal)
                                sl = round(curr_price - (1.2 * atr), 1) if signal=="CE" else round(curr_price + (1.2 * atr), 1)
                                tg = round(curr_price + (2.5 * atr), 1) if signal=="CE" else round(curr_price - (2.5 * atr), 1)
                                
                                current_trade = {"type": signal, "entry": curr_price, "strike": strike, "sl": sl, "tg": tg}
                                msg = (f"🚀 *ENTRY SIGNAL: NIFTY {signal}*\n\n"
                                       f"📍 Index Entry: {curr_price}\n"
                                       f"🎯 Strike: {strike} {signal}\n"
                                       f"🛑 SL: {sl} | 🏁 TG: {tg}")
                                send_telegram_msg(msg)
                                trade_active = True

                        # --- EXIT ---
                        else:
                            exit_triggered = False
                            reason = ""
                            if current_trade["type"] == "CE":
                                if curr_price <= current_trade["sl"]: exit_triggered, reason = True, "SL Hit 🛑"
                                elif curr_price >= current_trade["tg"]: exit_triggered, reason = True, "Target Hit 🏁"
                            else: # PE
                                if curr_price >= current_trade["sl"]: exit_triggered, reason = True, "SL Hit 🛑"
                                elif curr_price <= current_trade["tg"]: exit_triggered, reason = True, "Target Hit 🏁"
                            
                            # Auto-exit at 3:10 PM
                            if now.time() >= dt.time(15, 10): 
                                exit_triggered, reason = True, "EOD Exit 🕒"

                            if exit_triggered:
                                pnl = round(curr_price - current_trade["entry"], 2) if current_trade["type"]=="CE" else round(current_trade["entry"] - curr_price, 2)
                                send_telegram_msg(f"🔔 *EXIT: {reason}*\nPrice: {curr_price}\nPoints: {pnl}")
                                trade_active = False
                
                elif "TooManyRequests" in str(res.get('message', '')):
                    print("🛑 Rate limit! Sleeping 70s...")
                    time.sleep(70)
                
            else:
                # Market Closed: Connection null kar do taaki kal naya bane
                if obj is not None:
                    obj = None
                    print("💤 Market Closed. Session Cleared.")
                time.sleep(300) # 5 min wait

        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(15)
        
        time.sleep(60) # Every 1 minute scan

if __name__ == "__main__":
    # 1. Start Scanner Thread
    t = threading.Thread(target=live_scanner_logic)
    t.daemon = True
    t.start()
    
    # 2. Start Flask Web Server
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

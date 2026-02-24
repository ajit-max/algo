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

# ================= CONFIG (Environment Variables use karein) =================
API_KEY = os.getenv("API_KEY", "yRe368gf")
CLIENT_ID = os.getenv("CLIENT_ID", "AABZ146183")
PASSWORD = os.getenv("PASSWORD", "6211")
TOTP_SECRET = os.getenv("TOTP_SECRET", "ZHFAFO7SKLYN3FNJOBPZYNEGQI")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8291109950:AAE-vcehleqwpl0Bc-2o1dlaUOEQNWw9r-4")
CHAT_ID = os.getenv("CHAT_ID", "1901759813")

INDEX_TOKEN = "99926000"  # NIFTY 50
# =====================================================================

# Flask Health Check Route (Render ko khush rakhne ke liye)
@app.route('/')
def home():
    return "Bot is Running! 🚀"

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
        obj.generateSession(CLIENT_ID, PASSWORD, totp)
        return obj
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None

def live_scanner_logic():
    obj = connect_angel()
    if not obj: return

    print("📡 Scanner Started... Monitoring NIFTY Live")
    trade_active = False
    current_trade = {}

    while True:
        now = dt.datetime.now()
        # Market Time Check
        if dt.time(9,15) <= now.time() <= dt.time(15,30):
            try:
                from_date = (now - dt.timedelta(days=7)).strftime("%Y-%m-%d 09:15")
                to_date = now.strftime("%Y-%m-%d %H:%M")
                
                res = obj.getCandleData({
                    "exchange": "NSE", "symboltoken": INDEX_TOKEN,
                    "interval": "FIVE_MINUTE", "fromdate": from_date, "todate": to_date
                })
                
                if res['status'] == False:
                    time.sleep(70)
                    continue

                if res['status'] and res['data']:
                    df = pd.DataFrame(res['data'], columns=['date','open','high','low','close','volume'])
                    df[['high','low','close']] = df[['high','low','close']].astype(float)
                    
                    if len(df) >= 201:
                        curr_price = df['close'].iloc[-1]
                        ema200 = talib.EMA(df['close'], 200).iloc[-1]
                        rsi = talib.RSI(df['close'], 14).iloc[-1]
                        atr = talib.ATR(df['high'], df['low'], df['close'], 14).iloc[-1]

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
                                msg = f"🚀 *ENTRY:* {signal_type} @ {curr_price}\nSL: {sl} | TG: {tg}"
                                send_telegram_msg(msg)
                                trade_active = True
                        else:
                            # Exit Logic (SL/Target/Time)
                            exit_triggered = False
                            if current_trade["type"] == "CE":
                                if curr_price <= current_trade["sl"] or curr_price >= current_trade["tg"]:
                                    exit_triggered = True
                            else:
                                if curr_price >= current_trade["sl"] or curr_price <= current_trade["tg"]:
                                    exit_triggered = True
                            
                            if now.time() >= dt.time(15, 10): exit_triggered = True

                            if exit_triggered:
                                send_telegram_msg(f"🔔 *EXIT:* {current_trade['type']} @ {curr_price}")
                                trade_active = False
                
            except Exception as e:
                print(f"⚠️ Error: {e}")
                time.sleep(10)
        
        time.sleep(60)

if __name__ == "__main__":
    # Scanner ko background thread mein start karein
    t = threading.Thread(target=live_scanner_logic)
    t.daemon = True
    t.start()
    
    # Render ke liye port fetch karein
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
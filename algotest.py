import pandas as pd
import datetime as dt
import talib
import time
import requests
import pyotp
import warnings
import os
import threading
from flask import Flask
from SmartApi import SmartConnect

warnings.filterwarnings("ignore")

# ================= PROFESSIONAL CONFIG =================
API_KEY = "yRe368gf"
CLIENT_ID = "AABZ146183"
PASSWORD = "6211"
TOTP_SECRET = "ZHFAFO7SKLYN3FNJOBPZYNEGQI"
TELEGRAM_TOKEN = "8291109950:AAE-vcehleqwpl0Bc-2o1dlaUOEQNWw9r-4"
CHAT_ID = "1901759813"
INDEX_TOKEN = "99926000" 
LOT_SIZE = 25
CAPITAL = 50000
RISK_PER_TRADE_PCT = 0.02  # 2% Risk
MAX_DAILY_LOSS = 3000      # Hard stop
PAPER_TRADE = True         # Live karne ke liye False karein
# =======================================================

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
    except: pass

def get_instrument_master():
    url = "https://margincalculator.angelbroking.com/OpenAPI_Standard/v1/instrumentsJSON.json"
    
    # FIX: Adding User-Agent and Error Handling so Angel One doesn't block Cloud IPs
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    for attempt in range(3):
        try:
            print(f"📥 Downloading Instrument Master (Attempt {attempt+1})...")
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                try:
                    res_json = res.json()
                    df = pd.DataFrame(res_json)
                    df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
                    print("✅ Instrument Master Success!")
                    return df
                except Exception as e:
                    print(f"⚠️ JSON Parse Error (Got HTML?): {e}")
            else:
                print(f"⚠️ Server returned status: {res.status_code}")
        except Exception as e:
            print(f"⚠️ Network Error: {e}")
        
        time.sleep(3) # Retry delay
    
    send_telegram("❌ FATAL: Could not fetch Instrument List from Angel One.")
    return None

def get_ohlc_data(obj, token, interval, days=5):
    now = dt.datetime.now()
    res = obj.getCandleData({
        "exchange": "NSE", "symboltoken": token, "interval": interval,
        "fromdate": (now - dt.timedelta(days=days)).strftime("%Y-%m-%d 09:15"),
        "todate": now.strftime("%Y-%m-%d %H:%M")
    })
    if res and res.get("data"):
        df = pd.DataFrame(res["data"], columns=['date','o','h','l','c','v'])
        df[['h','l','c']] = df[['h','l','c']].astype(float)
        return df
    return None

def get_atm_option(df_master, spot_price, opt_type):
    strike = round(spot_price / 50) * 50
    df = df_master[(df_master['name'] == 'NIFTY') & 
                   (df_master['instrumenttype'] == 'OPTIDX') & 
                   (df_master['symbol'].str.endswith(opt_type)) & 
                   (df_master['strike'].astype(float) == float(strike * 100))].copy()
    df = df[df['expiry'] >= dt.datetime.now()].sort_values(by='expiry')
    return (df.iloc[0]['token'], df.iloc[0]['symbol']) if not df.empty else (None, None)

def run_pro_engine():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    obj.generateSession(CLIENT_ID, PASSWORD, totp)
    
    df_master = get_instrument_master()
    if df_master is None:
        print("❌ Stopping bot. Fix Instrument Master issue.")
        return # Prevents the bot from running without data

    trade_active = False
    trade = {}
    daily_pnl = 0
    
    send_telegram("💎 PRO-BOT ACTIVE\nStrategy: 15m Trend + 5m Momentum")

    while True:
        now = dt.datetime.now()
        if not (dt.time(9,20) <= now.time() <= dt.time(15,10)):
            time.sleep(30); continue
            
        if daily_pnl <= -MAX_DAILY_LOSS:
            send_telegram("⚠️ Max Daily Loss Reached. System Shutdown.")
            break

        try:
            # 1. Higher Timeframe Trend (15 Min)
            df_15 = get_ohlc_data(obj, INDEX_TOKEN, "FIFTEEN_MINUTE")
            ema_200_15 = talib.EMA(df_15['c'], 200).iloc[-1]
            current_spot = df_15['c'].iloc[-1]
            
            trend = "BULL" if current_spot > ema_200_15 else "BEAR"

            # 2. Execution Timeframe (5 Min)
            df_5 = get_ohlc_data(obj, INDEX_TOKEN, "FIVE_MINUTE")
            rsi_5 = talib.RSI(df_5['c'], 14).iloc[-1]

            # 3. ENTRY LOGIC (Trend + Momentum)
            if not trade_active:
                direction = None
                if trend == "BULL" and rsi_5 > 65: direction = "CE"
                elif trend == "BEAR" and rsi_5 < 35: direction = "PE"

                if direction:
                    token, symbol = get_atm_option(df_master, current_spot, direction)
                    opt_ltp = float(obj.ltpData("NFO", symbol, token)["data"]["ltp"])
                    
                    # Risk Management
                    sl_points = opt_ltp * 0.15 # 15% Initial SL
                    qty = int((CAPITAL * RISK_PER_TRADE_PCT) // (sl_points * LOT_SIZE)) * LOT_SIZE
                    
                    if qty >= LOT_SIZE:
                        # Place Market Order
                        if not PAPER_TRADE:
                            obj.placeOrder({"variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                                          "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                                          "producttype": "INTRADAY", "duration": "DAY", "quantity": str(qty)})
                        
                        trade = {"symbol": symbol, "token": token, "entry": opt_ltp, "qty": qty, 
                                 "sl": opt_ltp - sl_points, "target": opt_ltp + (sl_points * 2)}
                        trade_active = True
                        send_telegram(f"🚀 TRADE TAKEN: {symbol}\nPrice: {opt_ltp}\nTarget: {round(trade['target'],2)}")

            # 4. EXIT & PRO TRAILING
            else:
                ltp = float(obj.ltpData("NFO", trade['symbol'], trade['token'])["data"]["ltp"])
                
                # Trailing Stop-Loss (Move to Break Even after 10% gain)
                if ltp > trade['entry'] * 1.10 and trade['sl'] < trade['entry']:
                    trade['sl'] = trade['entry']
                    send_telegram(f"🛡️ SL Moved to Cost for {trade['symbol']}")

                reason = None
                if ltp <= trade['sl']: reason = "StopLoss Hit"
                elif ltp >= trade['target']: reason = "Target Hit 🎯"
                elif now.time() >= dt.time(15,10): reason = "EOD Square-off"

                if reason:
                    if not PAPER_TRADE:
                        obj.placeOrder({"variety": "NORMAL", "tradingsymbol": trade['symbol'], "symboltoken": trade['token'],
                                      "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                                      "producttype": "INTRADAY", "duration": "DAY", "quantity": str(trade['qty'])})
                    
                    pnl = (ltp - trade['entry']) * trade['qty']
                    daily_pnl += pnl
                    send_telegram(f"🏁 EXIT: {reason}\nPnL: ₹{round(pnl,2)}")
                    trade_active = False

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)
        
        time.sleep(30)


# ================= FLASK SERVER (Render Keep-Alive) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Algo Trading Bot is Running Live!"

def start_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # 1. Flask ko background thread mein chalu karein
    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    # 2. Apna bot chalu karein
    run_pro_engine()

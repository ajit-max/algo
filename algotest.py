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

def log_msg(msg, send_tg=True):
    """Logs to console and optionally sends to Telegram"""
    print(f"\n[{dt.datetime.now().strftime('%H:%M:%S')}] 📢 {msg}", flush=True)
    if send_tg:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        except Exception as e:
            print(f"⚠️ Telegram Error: {e}", flush=True)

def get_instrument_master():
    # 🔥 FIX: 100% Correct Angel Broking Server URL 🔥
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    for attempt in range(3):
        try:
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] 📥 Downloading Instrument Master (Attempt {attempt+1})...", flush=True)
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                try:
                    df = pd.DataFrame(res.json())
                    df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
                    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ✅ Instrument Master Downloaded Successfully! Total symbols: {len(df)}", flush=True)
                    return df
                except Exception as e:
                    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ⚠️ JSON Parse Error: {e}", flush=True)
            else:
                print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ⚠️ Server returned status: {res.status_code}", flush=True)
        except Exception as e:
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ⚠️ Network Error: {e}", flush=True)
        time.sleep(3)
    
    log_msg("❌ FATAL: Could not fetch Instrument List from Angel One.")
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
    print("\n" + "="*50, flush=True)
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] 🚀 INITIALIZING SMARTAPI CONNECTION...", flush=True)
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    obj.generateSession(CLIENT_ID, PASSWORD, totp)
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ✅ CONNECTION SUCCESSFUL!", flush=True)
    print("="*50 + "\n", flush=True)
    
    df_master = get_instrument_master()
    if df_master is None:
        print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ❌ Stopping bot. Fix Instrument Master issue.", flush=True)
        return

    trade_active = False
    trade = {}
    daily_pnl = 0
    
    log_msg("💎 PRO-BOT ACTIVE\nStrategy: 15m Trend + 5m Momentum\nMode: " + ("PAPER" if PAPER_TRADE else "LIVE"))

    while True:
        now = dt.datetime.now()
        
        # 1. Market Time Check (Prints continuously even if market is closed)
        if not (dt.time(9,20) <= now.time() <= dt.time(15,10)):
            print(f"[{now.strftime('%H:%M:%S')}] 💤 💓 Bot Alive - Market Closed. Waiting...", flush=True)
            time.sleep(30) # Har 30 sec me zinda hone ka sabut dega
            continue
            
        if daily_pnl <= -MAX_DAILY_LOSS:
            log_msg("⚠️ Max Daily Loss Reached. System Shutdown.")
            break

        try:
            # 2. Get Data
            df_15 = get_ohlc_data(obj, INDEX_TOKEN, "FIFTEEN_MINUTE")
            df_5 = get_ohlc_data(obj, INDEX_TOKEN, "FIVE_MINUTE")
            
            if df_15 is None or df_5 is None:
                print(f"[{now.strftime('%H:%M:%S')}] ⚠️ API returned empty data. Retrying...", flush=True)
                time.sleep(10); continue

            # Technicals
            ema_200_15 = talib.EMA(df_15['c'], 200).iloc[-1]
            current_spot = df_15['c'].iloc[-1]
            rsi_5 = talib.RSI(df_5['c'], 14).iloc[-1]
            trend = "BULL 🟢" if current_spot > ema_200_15 else "BEAR 🔴"

            # === LIVE HEARTBEAT LOG (Yahan se aapko har 30 sec ka update milega) ===
            status = f"ACTIVE ({trade['symbol']})" if trade_active else "SEARCHING 🔍"
            print(f"[{now.strftime('%H:%M:%S')}] 💓 Bot Alive | Spot: {current_spot} | 15m Trend: {trend} | 5m RSI: {round(rsi_5, 2)} | Status: {status}", flush=True)

            # 3. ENTRY LOGIC
            if not trade_active:
                direction = None
                if "BULL" in trend and rsi_5 > 65: direction = "CE"
                elif "BEAR" in trend and rsi_5 < 35: direction = "PE"

                if direction:
                    token, symbol = get_atm_option(df_master, current_spot, direction)
                    if token is None: 
                        continue
                    
                    opt_ltp = float(obj.ltpData("NFO", symbol, token)["data"]["ltp"])
                    
                    sl_points = opt_ltp * 0.15 
                    qty = int((CAPITAL * RISK_PER_TRADE_PCT) // (sl_points * LOT_SIZE)) * LOT_SIZE
                    
                    if qty >= LOT_SIZE:
                        if not PAPER_TRADE:
                            obj.placeOrder({"variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                                          "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                                          "producttype": "INTRADAY", "duration": "DAY", "quantity": str(qty)})
                        
                        trade = {"symbol": symbol, "token": token, "entry": opt_ltp, "qty": qty, 
                                 "sl": opt_ltp - sl_points, "target": opt_ltp + (sl_points * 2)}
                        trade_active = True
                        log_msg(f"🚀 ENTRY SIGNAL: {symbol}\nPrice: {opt_ltp}\nSL: {round(trade['sl'],2)}\nTarget: {round(trade['target'],2)}\nQty: {qty}")

            # 4. EXIT & PRO TRAILING
            else:
                ltp = float(obj.ltpData("NFO", trade['symbol'], trade['token'])["data"]["ltp"])
                print(f"   ↳ [TRADE LIVE] Target: {round(trade['target'],2)} | LTP: {ltp} | SL: {round(trade['sl'],2)}", flush=True)
                
                # Trailing SL
                if ltp > trade['entry'] * 1.10 and trade['sl'] < trade['entry']:
                    trade['sl'] = trade['entry']
                    log_msg(f"🛡️ SL Trailed to Cost ({trade['entry']}) for {trade['symbol']}")

                reason = None
                if ltp <= trade['sl']: reason = "StopLoss Hit 🛑"
                elif ltp >= trade['target']: reason = "Target Hit 🎯"
                elif now.time() >= dt.time(15,10): reason = "EOD Square-off 🕒"

                if reason:
                    if not PAPER_TRADE:
                        obj.placeOrder({"variety": "NORMAL", "tradingsymbol": trade['symbol'], "symboltoken": trade['token'],
                                      "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                                      "producttype": "INTRADAY", "duration": "DAY", "quantity": str(trade['qty'])})
                    
                    pnl = (ltp - trade['entry']) * trade['qty']
                    daily_pnl += pnl
                    log_msg(f"🏁 EXIT ALERT: {reason}\nSymbol: {trade['symbol']}\nExit Price: {ltp}\nTrade PnL: ₹{round(pnl,2)}\nTotal Daily PnL: ₹{round(daily_pnl,2)}")
                    trade_active = False

        except Exception as e:
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] ⚠️ Loop Error: {e}", flush=True)
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
    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    run_pro_engine()

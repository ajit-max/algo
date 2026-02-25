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
LOG_FILE = "bot_logs.txt"  # Live web logs ke liye file
# =======================================================

def custom_print(msg, send_tg=False):
    """Console print + Web Log File save + Optional Telegram"""
    time_str = dt.datetime.now().strftime('%H:%M:%S')
    full_msg = f"[{time_str}] {msg}"
    
    # 1. Print in Render Dashboard
    print(full_msg, flush=True)
    
    # 2. Save for Web Browser (/logs URL)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(full_msg + "<br>")
    except: pass
    
    # 3. Send to Telegram
    if send_tg:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": f"📢 {msg}"}, timeout=5)
        except Exception as e:
            print(f"[{time_str}] ⚠️ Telegram Error: {e}", flush=True)

def get_instrument_master():
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for attempt in range(3):
        try:
            custom_print(f"📥 Downloading Instrument Master (Attempt {attempt+1})...")
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                df = pd.DataFrame(res.json())
                df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
                custom_print(f"✅ Instrument Master Downloaded Successfully! Total symbols: {len(df)}")
                return df
            else:
                custom_print(f"⚠️ Server returned status: {res.status_code}")
        except Exception as e:
            custom_print(f"⚠️ Network Error: {e}")
        time.sleep(3)
    
    custom_print("❌ FATAL: Could not fetch Instrument List.", send_tg=True)
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
    # Pehle purani log file delete kar dete hain naye session ke liye
    if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
    
    custom_print("="*50)
    custom_print("🚀 INITIALIZING SMARTAPI CONNECTION...")
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    obj.generateSession(CLIENT_ID, PASSWORD, totp)
    custom_print("✅ CONNECTION SUCCESSFUL!")
    custom_print("="*50)
    
    df_master = get_instrument_master()
    if df_master is None:
        custom_print("❌ Stopping bot. Fix Instrument Master issue.")
        return

    trade_active = False
    trade = {}
    daily_pnl = 0
    
    custom_print("💎 PRO-BOT ACTIVE | Strategy: 15m Trend + 5m Momentum", send_tg=True)

    while True:
        now = dt.datetime.now()
        
        # Market Time Check
        if not (dt.time(9,20) <= now.time() <= dt.time(15,10)):
            custom_print("💤 💓 Bot Alive - Market Closed. Waiting...")
            time.sleep(30)
            continue
            
        if daily_pnl <= -MAX_DAILY_LOSS:
            custom_print("⚠️ Max Daily Loss Reached. System Shutdown.", send_tg=True)
            break

        try:
            df_15 = get_ohlc_data(obj, INDEX_TOKEN, "FIFTEEN_MINUTE")
            df_5 = get_ohlc_data(obj, INDEX_TOKEN, "FIVE_MINUTE")
            
            if df_15 is None or df_5 is None:
                custom_print("⚠️ API returned empty data. Retrying...")
                time.sleep(10); continue

            # Technicals
            ema_200_15 = talib.EMA(df_15['c'], 200).iloc[-1]
            current_spot = df_15['c'].iloc[-1]
            rsi_5 = talib.RSI(df_5['c'], 14).iloc[-1]
            trend = "BULL 🟢" if current_spot > ema_200_15 else "BEAR 🔴"

            # === LIVE HEARTBEAT ===
            status = f"ACTIVE ({trade['symbol']})" if trade_active else "SEARCHING 🔍"
            custom_print(f"💓 Bot Alive | Spot: {current_spot} | 15m Trend: {trend} | 5m RSI: {round(rsi_5, 2)} | Status: {status}")

            # 3. ENTRY LOGIC
            if not trade_active:
                direction = None
                if "BULL" in trend and rsi_5 > 65: direction = "CE"
                elif "BEAR" in trend and rsi_5 < 35: direction = "PE"

                if direction:
                    token, symbol = get_atm_option(df_master, current_spot, direction)
                    if token is None: continue
                    
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
                        custom_print(f"🚀 ENTRY SIGNAL: {symbol} | Price: {opt_ltp} | Qty: {qty}", send_tg=True)

            # 4. EXIT & PRO TRAILING
            else:
                ltp = float(obj.ltpData("NFO", trade['symbol'], trade['token'])["data"]["ltp"])
                custom_print(f"↳ [TRADE LIVE] Target: {round(trade['target'],2)} | LTP: {ltp} | SL: {round(trade['sl'],2)}")
                
                # Trailing SL
                if ltp > trade['entry'] * 1.10 and trade['sl'] < trade['entry']:
                    trade['sl'] = trade['entry']
                    custom_print(f"🛡️ SL Trailed to Cost ({trade['entry']}) for {trade['symbol']}", send_tg=True)

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
                    custom_print(f"🏁 EXIT ALERT: {reason} | Symbol: {trade['symbol']} | PnL: ₹{round(pnl,2)}", send_tg=True)
                    trade_active = False

        except Exception as e:
            custom_print(f"⚠️ Loop Error: {e}")
            time.sleep(10)
        
        time.sleep(30)


# ================= FLASK SERVER (Web Logs System) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "<h1>✅ Algo Trading Bot is Running Live!</h1><p>Go to <a href='/logs'>/logs</a> to see live terminal output.</p>"

@app.route('/logs')
def show_logs():
    try:
        with open(LOG_FILE, "r") as f:
            # Sirf last 100 lines dikhayega taaki page load hone me time na lage
            lines = f.readlines()[-100:]
            content = "".join(lines)
    except:
        content = "Waiting for bot to generate logs..."
        
    # Ek cool 'Hacker' style black screen look
    html = f"""
    <html>
        <head>
            <title>Live Bot Logs</title>
            <meta http-equiv="refresh" content="30"> </head>
        <body style='background-color:black; color:lime; font-family:monospace; padding: 20px;'>
            <h2>🚀 LIVE TRADING TERMINAL</h2>
            <hr style='border-color:lime;'>
            {content}
        </body>
    </html>
    """
    return html

def start_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    run_pro_engine()

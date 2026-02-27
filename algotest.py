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
SESSION_REFRESH_HOURS  = 5        # Changed to 5 for safety
INSTRUMENT_REFRESH_MIN = 60
MAX_TRADES_PER_DAY     = 3        # Control System Max Trades
# ============================================================================

# ╔══════════════════════════════════════════════════════════════════╗
# ║    TIME FILTER CONFIG & FUNCTION                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
TRADING_START      = dt.time(9, 25)    
TRADING_HARD_STOP  = dt.time(15, 15)   

def is_in_trading_window(now, index_name="NIFTY"):
    t = now.time()

    if now.weekday() >= 5:
        return False, "Weekend — market closed"
    if t < TRADING_START:
        return False, f"Pre-market — entries open at {TRADING_START}"
    if t >= TRADING_HARD_STOP:
        return False, f"Hard stop — no new entries after {TRADING_HARD_STOP}"

    return True, f"Full trading session active ({TRADING_START}–{TRADING_HARD_STOP})"

# ======================== CORE FUNCTIONS ========================
def custom_print(msg, send_tg=False):
    time_str = dt.datetime.now().strftime('%H:%M:%S')
    
    if "\n" in msg:
        log_msg = f"[{time_str}] " + msg.replace("\n", " | ")
    else:
        log_msg = f"[{time_str}] {msg}"
        
    print(log_msg, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(log_msg + "<br>\n")
    except Exception: pass
    
    if send_tg:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        except Exception as e:
            pass

def create_session():
    for attempt in range(3):
        try:
            custom_print(f"SYSTEM: Initializing SmartAPI Session (Attempt {attempt + 1})...")
            obj  = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET).now()
            data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
            if data and data.get("status"):
                custom_print("SYSTEM: Session Established Successfully.")
                return obj
            else:
                custom_print(f"SYSTEM WARNING: Session Response: {data}")
        except Exception as e:
            custom_print(f"SYSTEM ERROR (Attempt {attempt + 1}): {e}")
        time.sleep(5)
    custom_print("FATAL ERROR: Failed to create session after 3 attempts.", send_tg=True)
    return None

def get_instrument_master():
    url     = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    headers = {'User-Agent': 'Mozilla/5.0'}
    for attempt in range(3):
        try:
            custom_print(f"SYSTEM: Downloading Instrument Master (Attempt {attempt + 1})...")
            res = requests.get(url, headers=headers, timeout=30)
            if res.status_code == 200:
                df            = pd.DataFrame(res.json())
                df['expiry']  = pd.to_datetime(df['expiry'], errors='coerce')
                df['strike']  = pd.to_numeric(df['strike'], errors='coerce')
                custom_print(f"SYSTEM: Instrument Master Ready. Total Symbols: {len(df)}")
                return df
        except Exception as e:
            custom_print(f"SYSTEM ERROR: Network Error: {e}")
        time.sleep(5)
    custom_print("FATAL ERROR: Failed to download Instrument Master.", send_tg=True)
    return None

def get_nifty_spot_token(df_master):
    try:
        df_nifty = df_master[(df_master['name'] == 'Nifty 50') & (df_master['instrumenttype'] == 'AMXIDX') & (df_master['exch_seg'] == 'NSE')]
        if not df_nifty.empty: return str(df_nifty.iloc[0]['token'])
        df_nifty2 = df_master[(df_master['symbol'] == 'Nifty 50') & (df_master['exch_seg'] == 'NSE')]
        if not df_nifty2.empty: return str(df_nifty2.iloc[0]['token'])
    except Exception as e:
        pass
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
        pass
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
        pass
    return None, None

def place_order(obj, symbol, token, transaction_type, qty):
    if PAPER_TRADE:
        custom_print(f"PAPER TRADE EXECUTION: {transaction_type} | {symbol} | Qty: {qty}")
        return True
    try:
        res = obj.placeOrder({
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
            "transactiontype": transaction_type, "exchange": "NFO", "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": str(qty)
        })
        custom_print(f"LIVE ORDER PLACED: {transaction_type} {qty} {symbol} | Response: {res}")
        return True
    except Exception as e:
        custom_print(f"ORDER ERROR: {e}", send_tg=True)
        return False

# ======================== MAIN TRADING LOOP ========================
session_start_time = dt.datetime.now()

def inner_trading_loop(obj, df_master_ref):
    global session_start_time
    trade_active           = False
    trade                  = {}
    daily_pnl              = 0
    trade_count            = 0
    last_exit_time         = None
    eod_locked             = False
    daily_loss_locked      = False
    last_trade_direction   = None
    current_trade_date     = dt.datetime.now().date()
    
    last_inst_refresh      = dt.datetime.now()
    last_time_block_reason = None
    entry_window_printed   = False

    nifty_token = get_nifty_spot_token(df_master_ref['df'])
    if nifty_token is None:
        custom_print("SYSTEM ERROR: NIFTY spot token missing. Terminating loop.")
        return "STOP"

    custom_print(f"SYSTEM ACTIVE | NIFTY Token: {nifty_token} | Qty: {TRADE_QTY}", send_tg=True)

    while True:
        now = dt.datetime.now()

        # ── STRUCTURED DAILY RESET ──
        if now.date() > current_trade_date:
            trade_count = 0
            daily_pnl = 0
            last_exit_time = None
            eod_locked = False
            daily_loss_locked = False
            last_trade_direction = None
            current_trade_date = now.date()
            entry_window_printed = False
            custom_print("SYSTEM RESET: Daily limits and constraints refreshed for the new trading session.")

        # ── TIME FILTER INTEGRATION ──
        in_window, window_reason = is_in_trading_window(now, index_name="NIFTY")

        if in_window and not entry_window_printed:
            custom_print("SYSTEM: Entry Window Active")
            entry_window_printed = True
        elif not in_window:
            entry_window_printed = False

        if not in_window:
            if not trade_active:
                if window_reason != last_time_block_reason:
                    custom_print(f"SYSTEM FILTER: {window_reason}")
                    last_time_block_reason = window_reason
                time.sleep(60)
                continue
        else:
            if last_time_block_reason is not None and not trade_active:
                custom_print(f"SYSTEM FILTER: Trading window open: {window_reason}")
                last_time_block_reason = None

        if daily_pnl <= -MAX_DAILY_LOSS:
            if not daily_loss_locked:
                custom_print("SYSTEM LOCKED – DAILY LOSS LIMIT HIT", send_tg=True)
                daily_loss_locked = True

        mins_since = (now - last_inst_refresh).total_seconds() / 60
        if mins_since >= INSTRUMENT_REFRESH_MIN:
            new_master = get_instrument_master()
            if new_master is not None:
                df_master_ref['df'] = new_master
                last_inst_refresh   = now
                new_token = get_nifty_spot_token(df_master_ref['df'])
                if new_token: nifty_token = new_token

        df_master = df_master_ref['df']

        try:
            # DO NOT CHANGE FETCH LOGIC
            df_15 = get_ohlc_data(obj, nifty_token, "FIFTEEN_MINUTE", days=15)
            df_5  = get_ohlc_data(obj, nifty_token, "FIVE_MINUTE", days=5)

            if df_15 is None or len(df_15) < 220:
                time.sleep(15)
                continue
            if df_5 is None or len(df_5) < 15:
                time.sleep(15)
                continue

            # ── INDICATOR CALCULATIONS ──
            ema_200_15   = talib.EMA(df_15['c'], 200).iloc[-1]
            
            atr_series   = talib.ATR(df_15['h'], df_15['l'], df_15['c'], 14)
            atr_14_15    = atr_series.iloc[-1]
            atr_avg      = talib.SMA(atr_series, 20).iloc[-1]
            
            current_spot = df_15['c'].iloc[-1]
            
            rsi_5_series = talib.RSI(df_5['c'], 14)
            rsi_prev     = rsi_5_series.iloc[-2]
            rsi_curr     = rsi_5_series.iloc[-1]
            
            trend = "BULL" if current_spot > ema_200_15 else "BEAR"

            # ── COOLDOWN CALCULATION ──
            cooldown_remaining = 0
            if last_exit_time is not None:
                elapsed_sec = (now - last_exit_time).total_seconds()
                if elapsed_sec < 600:
                    cooldown_remaining = int(600 - elapsed_sec)
            
            cooldown_str = f"{cooldown_remaining//60}m {cooldown_remaining%60}s" if cooldown_remaining > 0 else "0s"

            # ── CLEAN HEARTBEAT FORMAT ──
            heartbeat_msg = (
                f"HEARTBEAT:\n"
                f"Time: {now.strftime('%H:%M:%S')} | Spot: {current_spot:.1f} | EMA: {ema_200_15:.1f} | RSI: {rsi_curr:.1f} | ATR: {atr_14_15:.1f} | Trend: {trend}\n"
                f"TradeActive: {trade_active} | TradeCount: {trade_count}/{MAX_TRADES_PER_DAY} | DailyPnL: Rs {daily_pnl:.2f} | CooldownRemaining: {cooldown_str} | EODLocked: {eod_locked} | LossLocked: {daily_loss_locked}"
            )
            custom_print(heartbeat_msg)

            # ── EXIT LOGIC ──
            if trade_active:
                ltp_res = obj.ltpData("NFO", trade['symbol'], trade['token'])
                if not ltp_res or not ltp_res.get("data"): 
                    time.sleep(15)
                    continue

                ltp = float(ltp_res["data"]["ltp"])
                
                # Risk Management: Breakeven at 1R
                if ltp >= (trade['entry'] + trade['sl_points']) and trade['sl'] < trade['entry']:
                    trade['sl'] = trade['entry']
                    custom_print(f"RISK CONTROL: SL modified to Breakeven at {trade['entry']} for {trade['symbol']}", send_tg=True)

                reason = None
                if ltp <= trade['sl']: reason = "StopLoss Hit"
                elif ltp >= trade['target']: reason = "Target Hit"
                elif now.time() >= dt.time(15, 20): reason = "EOD Square-off"

                if reason:
                    place_order(obj, trade['symbol'], trade['token'], "SELL", trade['qty'])
                    pnl       = (ltp - trade['entry']) * trade['qty']
                    daily_pnl += pnl
                    last_exit_time = now
                    
                    exit_msg = (
                        f"📊 Position: {trade['symbol']}\n"
                        f"🚪 Exit Price: {ltp}\n"
                        f"💰 PnL: Rs {round(pnl, 2)} (Daily: Rs {round(daily_pnl, 2)})\n"
                        f"📢 Action: {reason} - Position Closed."
                    )
                    custom_print(exit_msg, send_tg=True)
                    
                    trade_active = False
                    trade        = {}
                    
                    # EOD LOCK SYSTEM
                    if reason == "EOD Square-off" or now.time() >= dt.time(15, 20):
                        eod_locked = True
                        custom_print("SYSTEM CONTROL: EOD Lock activated. No further entries permitted today.")

            # ── ENTRY LOGIC ──
            elif not trade_active and in_window and not eod_locked and not daily_loss_locked:
                extreme_vol_threshold = current_spot * 0.01
                
                # Professional Trade Control Filters
                if trade_count >= MAX_TRADES_PER_DAY:
                    pass 
                elif now.time() >= dt.time(15, 15): 
                    pass # Hard Entry Cut-off
                elif cooldown_remaining > 0:
                    pass # 10 Minute Cooldown Active
                elif atr_14_15 < atr_avg:
                    pass # Skip entry (low volatility environment)
                elif atr_14_15 > extreme_vol_threshold:
                    pass # Skip entry (extreme volatility spike)
                else:
                    direction = None
                    if current_spot > ema_200_15 and rsi_prev <= 60 and rsi_curr > 60: 
                        if last_trade_direction != "CE":
                            direction = "CE"
                    elif current_spot < ema_200_15 and rsi_prev >= 40 and rsi_curr < 40: 
                        if last_trade_direction != "PE":
                            direction = "PE"

                    if direction:
                        custom_print(f"PRE-ENTRY CHECK: Trend={trend} | RSI={rsi_curr:.1f} | ATR={atr_14_15:.1f} | TradeCount={trade_count}/{MAX_TRADES_PER_DAY} | Cooldown={cooldown_str}")
                        
                        token, symbol = get_atm_option(df_master, current_spot, direction)
                        if not token: 
                            time.sleep(30)
                            continue

                        ltp_res = obj.ltpData("NFO", symbol, token)
                        if not ltp_res or not ltp_res.get("data"): 
                            time.sleep(15)
                            continue

                        opt_ltp   = float(ltp_res["data"]["ltp"])
                        
                        risk_points = atr_14_15 * 0.5
                        sl_points = opt_ltp * 0.12  # slightly tighter
                        tgt_points = sl_points * 2.2
                        qty       = TRADE_QTY

                        success = place_order(obj, symbol, token, "BUY", qty)
                        if success:
                            trade = {
                                "symbol": symbol, "token": token, "entry": opt_ltp, "qty": qty,
                                "sl_points": sl_points,
                                "sl": round(opt_ltp - sl_points, 2), 
                                "target": round(opt_ltp + tgt_points, 2)
                            }
                            trade_active = True
                            trade_count += 1
                            last_trade_direction = direction
                            
                            call_put_text = "CE" if direction == "CE" else "PE"
                            time_str = now.strftime('%I:%M %p')
                            entry_msg = (
                                f"🚀 ENTRY SIGNAL: NIFTY {call_put_text}\n\n"
                                f"📍 Index Spot: {current_spot:.1f}\n"
                                f"🎯 Buy: {symbol} @ {opt_ltp}\n"
                                f"🛑 StopLoss: {trade['sl']}\n"
                                f"🏁 Target: {trade['target']}\n"
                                f"🕒 Time: {time_str}"
                            )
                            custom_print(entry_msg, send_tg=True)
                            if trade_count >= MAX_TRADES_PER_DAY:
                                custom_print("SYSTEM CONTROL: Daily trade limit reached (3/3).")

        except Exception as e:
            custom_print(f"SYSTEM ERROR: Main Loop Exception: {e}")
            time.sleep(15)
            continue

        time.sleep(30)
        
        # ── SESSION SAFETY ──
        if dt.time(15, 10) <= now.time() <= dt.time(15, 25):
            pass 
        else:
            hours_since = (dt.datetime.now() - session_start_time).total_seconds() / 3600
            if hours_since >= SESSION_REFRESH_HOURS:
                custom_print("SYSTEM: Executing Scheduled Session Refresh...")
                return "REFRESH_SESSION"

    return "STOP"

# ======================== OUTER ENGINE ========================
def run_pro_engine():
    global session_start_time
    if os.path.exists(LOG_FILE): os.remove(LOG_FILE)

    custom_print("=" * 55)
    custom_print("SYSTEM STARTING: ALGO TRADING ENGINE")
    custom_print(f"Paper Mode: {PAPER_TRADE} | Qty: {TRADE_QTY} | Max Daily Loss: Rs{MAX_DAILY_LOSS}")
    custom_print("=" * 55)

    df_master = get_instrument_master()
    if df_master is None:
        custom_print("FATAL ERROR: Instrument Master mapping failed.")
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
            time.sleep(2)
            continue
        elif result == "STOP":
            now      = dt.datetime.now()
            next_day = (now + dt.timedelta(days=1)).replace(hour=9, minute=10, second=0)
            sleep_sec = (next_day - now).total_seconds()
            custom_print(f"SYSTEM OFF: Market closed. Resuming at {next_day.strftime('%d-%m-%Y %H:%M')}")
            time.sleep(max(sleep_sec, 3600))
            continue

# ======================== FLASK WEB SERVER ========================
app = Flask(__name__)

@app.route('/')
def home():
    mode = "PAPER MODE" if PAPER_TRADE else "LIVE MODE"
    return f"<h1>Algo Bot Active | {mode}</h1><p><a href='/logs'>View Live Logs</a></p><p>Qty: {TRADE_QTY} | Max Daily Loss: Rs{MAX_DAILY_LOSS}</p>"

@app.route('/logs')
def show_logs():
    try:
        with open(LOG_FILE, "r") as f:
            lines   = f.readlines()[-150:]
            content = "".join(lines)
    except Exception:
        content = "Logs initializing..."

    mode_color = "yellow" if PAPER_TRADE else "red"
    mode_text  = "PAPER" if PAPER_TRADE else "LIVE"

    html = f"""
    <html>
        <head>
            <title>System Logs</title>
            <meta http-equiv="refresh" content="30">
        </head>
        <body style='background:black; color:lime; font-family:monospace; padding:20px;'>
            <h2>LIVE TRADING TERMINAL</h2>
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
    custom_print(f"SYSTEM: Web interface active on port {os.environ.get('PORT', 10000)}")
    run_pro_engine()

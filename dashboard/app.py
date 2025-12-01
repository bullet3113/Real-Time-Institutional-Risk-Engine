import streamlit as st
import pandas as pd
import numpy as np
import time
import threading
import sys
import os
import pickle
from datetime import datetime

# Allow imports from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine.stream import run_stream_processor
from engine.warmup import run_warmup
from logic.risk_manager import RiskManager
from db_config import get_redis_connection

st.set_page_config(layout="wide", page_title="Institutional Risk Dashboard")

# ==========================================
# 0. CLOUD CONNECTION CHECK (CRITICAL)
# ==========================================
try:
    # Test connection immediately
    r = get_redis_connection()
    r.ping()
except Exception as e:
    st.error(f"🚨 FATAL ERROR: Cannot Connect to Redis.")
    st.code(str(e))
    st.info("Did you add 'REDIS_URL' to your Streamlit Secrets?")
    st.stop()

# ==========================================
# 1. BACKGROUND PROCESS MANAGER
# ==========================================
if 'stream_thread' not in st.session_state:
    st.session_state.stream_thread = None

def start_background_thread():
    if st.session_state.stream_thread is None or not st.session_state.stream_thread.is_alive():
        print("☁️ Starting Background Stream...")
        t = threading.Thread(target=run_stream_processor, daemon=True)
        t.start()
        st.session_state.stream_thread = t

# ==========================================
# 2. UI LAYOUT
# ==========================================
st.title("🛡️ Institutional Risk Engine (Cloud)")

# --- DEBUG CONTROLS ---
with st.expander("🛠️ Admin / Debugger", expanded=True):
    col_debug1, col_debug2 = st.columns(2)
    
    with col_debug1:
        st.write("**Background Thread Status:**")
        # Check thread health
        if st.session_state.stream_thread and st.session_state.stream_thread.is_alive():
            st.success("Thread is Running ✅")
            
            # Check Heartbeat
            hb = r.get("stream:heartbeat")
            err = r.get("stream:error")
            
            if hb: st.caption(f"Last Heartbeat: {hb.decode()}")
            if err: st.error(f"Stream Error: {err.decode()}")
            
        else:
            st.warning("Thread is Stopped ❌")
            if st.button("Start Background Stream"):
                # Check warmup
                if not r.exists("portfolio:cash"):
                    with st.spinner("Running First-Time Warmup (Downloading Data)..."):
                        run_warmup()
                start_background_thread()
                st.rerun()

    with col_debug2:
        st.write("**Foreground Test:**")
        run_foreground = st.checkbox("🔥 Run Stream in Foreground (Blocks UI)")
        st.caption("Use this if background thread fails. It forces the script to generate data right here.")

# --- FOREGROUND EXECUTION LOOP ---
if run_foreground:
    st.warning("Running in Foreground Mode. Uncheck box to stop.")
    placeholder = st.empty()
    # Import logic directly to avoid import errors
    from engine.stream import update_covariance_ewma, MockDataStream, TICKERS, LAMBDA_DECAY
    
    # Load state once
    cov_matrix_bytes = r.get("risk:cov_matrix:current")
    prices_bytes = r.get("market_data:last_prices")
    
    if cov_matrix_bytes and prices_bytes:
        current_cov_matrix = pickle.loads(cov_matrix_bytes)
        last_prices_dict = pickle.loads(prices_bytes)
        last_prices = np.array([last_prices_dict[t] for t in TICKERS])
        stream = MockDataStream(last_prices)
        
        # Fast Loop
        while run_foreground:
            new_prices = stream.get_next_tick()
            returns = np.log(new_prices / last_prices)
            new_cov_matrix = update_covariance_ewma(current_cov_matrix, returns, LAMBDA_DECAY)
            
            # Write
            r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
            price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
            r.set("market_data:last_prices", pickle.dumps(price_dict))
            r.set("stream:heartbeat", datetime.now().strftime("%H:%M:%S"))
            
            # Update Local Vars
            last_prices = new_prices
            current_cov_matrix = new_cov_matrix
            
            placeholder.success(f"Generated Tick: {new_prices[0]:.2f} at {datetime.now().strftime('%H:%M:%S')}")
            time.sleep(1)
    else:
        st.error("Warmup data missing. Click 'Start Background Stream' first to seed DB.")

# ==========================================
# 3. DASHBOARD VIEW
# ==========================================
rm = RiskManager()
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# Auto-Refresh Logic (Non-blocking)
if not run_foreground:
    if st.button("🔄 Refresh Data"):
        st.rerun()

# --- LIVE PRICES ---
st.subheader("Live Prices")
cov_matrix, prices = rm.get_market_data()

if prices is not None:
    cols = st.columns(len(TICKERS))
    for i, ticker in enumerate(TICKERS):
        cols[i].metric(label=ticker, value=f"${prices[i]:.2f}")
else:
    st.info("Waiting for data...")

st.divider()

# --- KPIS ---
data = rm.get_dashboard_metrics()
if data:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Net Liq", f"${data['total_value']:,.0f}")
    k2.metric("Cash", f"${data['cash']:,.0f}")
    k3.metric("VaR", f"${data['port_var']:,.0f}", f"Limit: ${data['limit']:,.0f}")
    k4.metric("Vol (Daily)", f"{data['port_std_daily']*100:.2f}%")

# --- TABLE ---
if data:
    st.subheader("Holdings")
    df = data['table_data']
    active_df = df[df['Qty'] > 0].copy()
    if not active_df.empty:
        st.dataframe(active_df.style.format({"Price": "${:,.2f}"}), use_container_width=True)
    else:
        st.caption("No active positions.")
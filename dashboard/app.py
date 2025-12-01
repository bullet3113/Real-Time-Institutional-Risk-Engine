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
# 0. SETUP & CONNECTIONS
# ==========================================
try:
    r = get_redis_connection()
    r.ping()
except Exception as e:
    st.error(f"🚨 Redis Connection Error: {e}")
    st.stop()

# Initialize Logic Helper
rm = RiskManager()
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# ==========================================
# 1. UI HEADER & CONTROLS
# ==========================================
st.title("🛡️ Institutional Risk Engine")

with st.expander("⚙️ Simulation Controls", expanded=True):
    col_ctrl1, col_ctrl2 = st.columns(2)
    with col_ctrl1:
        st.info("Market Data Source: Monte Carlo Simulation (Geometric Brownian Motion)")
    with col_ctrl2:
        run_foreground = st.toggle("🟢 Activate Real-Time Simulation", value=True)
        if run_foreground:
            st.caption("Engine is generating live ticks...")
        else:
            st.caption("Simulation paused.")

st.divider()

# ==========================================
# 2. PLACEHOLDERS (Where data will appear)
# ==========================================
# A. Prices
st.subheader("🔴 Live Market Prices")
ticker_cols = st.columns(len(TICKERS))
metric_placeholders = [col.empty() for col in ticker_cols]

st.divider()

# B. KPIs
st.subheader("🏦 Account Summary")
kpi_cols = st.columns(4)
kpi_placeholders = [col.empty() for col in kpi_cols]

st.divider()

# C. Tables
col_table, col_matrix = st.columns([1.5, 1])
with col_table:
    st.subheader("💼 Holdings")
    table_placeholder = st.empty()
with col_matrix:
    st.subheader("🧠 Correlations")
    matrix_placeholder = st.empty()

# ==========================================
# 3. SIDEBAR: EXECUTION BLOTTER (CRITICAL: MUST BE HERE)
# ==========================================
st.sidebar.header("⚡ Execution Blotter")

if 'trade_stage' not in st.session_state:
    st.session_state.trade_stage = 'input'

def reset_trade():
    st.session_state.trade_stage = 'input'
    st.session_state.trade_proposal = None

# --- INPUT STAGE ---
if st.session_state.trade_stage == 'input':
    with st.sidebar.form("trade_input"):
        ticker = st.selectbox("Ticker", TICKERS)
        side = st.selectbox("Side", ["BUY", "SELL"])
        qty = st.number_input("Quantity", min_value=1, value=100)
        
        if st.form_submit_button("Check Risk"):
            impact = rm.check_trade_impact(ticker, qty, side)
            st.session_state.trade_proposal = {"ticker": ticker, "qty": qty, "side": side, "impact": impact}
            st.session_state.trade_stage = 'confirm'
            st.rerun()

# --- CONFIRM STAGE ---
elif st.session_state.trade_stage == 'confirm':
    proposal = st.session_state.trade_proposal
    impact = proposal['impact']
    
    st.sidebar.info(f"Confirm {proposal['side']} {proposal['qty']} {proposal['ticker']}?")
    
    if impact['status'] == "APPROVED":
        st.sidebar.success("✅ APPROVED")
        st.sidebar.write(f"New VaR: ${impact['post_trade_var']:.0f}")
        
        col_a, col_b = st.sidebar.columns(2)
        if col_a.button("EXECUTE"):
            rm.execute_trade(proposal['ticker'], proposal['qty'], proposal['side'])
            st.toast("Trade Executed Successfully!", icon="🚀")
            time.sleep(1)
            reset_trade()
            st.rerun()
            
        if col_b.button("CANCEL"):
            reset_trade()
            st.rerun()
    else:
        st.sidebar.error(f"❌ BLOCKED: {impact.get('reason')}")
        if st.sidebar.button("Back"):
            reset_trade()
            st.rerun()

# ==========================================
# 4. DATA RENDERING HELPER
# ==========================================
def render_dashboard():
    """Fetches data from Redis and updates all placeholders."""
    # A. Market Data
    cov_matrix, prices = rm.get_market_data()
    
    if prices is not None:
        for i, ticker in enumerate(TICKERS):
            metric_placeholders[i].metric(label=ticker, value=f"${prices[i]:.2f}")
    
    # B. Account KPIs
    data = rm.get_dashboard_metrics()
    if data:
        kpi_placeholders[0].metric("Net Liq", f"${data['total_value']:,.0f}")
        kpi_placeholders[1].metric("Cash", f"${data['cash']:,.0f}")
        kpi_placeholders[2].metric("VaR", f"${data['port_var']:,.0f}", f"Limit: ${data['limit']:,.0f}")
        kpi_placeholders[3].metric("Vol (Daily)", f"{data['port_std_daily']*100:.2f}%")

        # C. Table
        df = data['table_data']
        active_df = df[df['Qty'] > 0].copy()
        if not active_df.empty:
            table_placeholder.dataframe(
                active_df.style.format({"Price": "${:,.2f}"}), 
                use_container_width=True
            )
        else:
            table_placeholder.info("No active positions.")
            
        # D. Matrix
        if cov_matrix is not None:
            std_devs = np.sqrt(np.diagonal(cov_matrix))
            std_devs[std_devs == 0] = 1e-9 
            corr_matrix = cov_matrix / np.outer(std_devs, std_devs)
            corr_df = pd.DataFrame(corr_matrix, index=TICKERS, columns=TICKERS)
            matrix_placeholder.dataframe(
                corr_df.style.background_gradient(cmap="RdYlGn_r", vmin=-1, vmax=1).format("{:.2f}"),
                use_container_width=True
            )

# ==========================================
# 5. EXECUTION LOOP
# ==========================================

if run_foreground:
    # --- SIMULATION MODE ---
    from engine.stream import update_covariance_ewma, MockDataStream, LAMBDA_DECAY
    
    # Load Initial State
    cov_matrix, prices = rm.get_market_data()
    
    # Auto-Warmup if empty
    if cov_matrix is None:
        with st.spinner("Initializing Database..."):
            run_warmup()
            cov_matrix, prices = rm.get_market_data()
            
    stream = MockDataStream(prices)
    current_cov_matrix = cov_matrix
    last_prices = prices

    # Fast Loop
    while run_foreground:
        # 1. Generate Data
        new_prices = stream.get_next_tick()
        returns = np.log(new_prices / last_prices)
        new_cov_matrix = update_covariance_ewma(current_cov_matrix, returns, LAMBDA_DECAY)
        
        # 2. Save to Redis
        r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
        price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
        r.set("market_data:last_prices", pickle.dumps(price_dict))
        
        # 3. Update Local Vars
        last_prices = new_prices
        current_cov_matrix = new_cov_matrix
        
        # 4. RENDER UI IMMEDIATELY
        render_dashboard()
        
        time.sleep(1.5)

else:
    # --- STATIC/PAUSED MODE ---
    render_dashboard()
    st.caption("Enable Simulation above to start data stream.")
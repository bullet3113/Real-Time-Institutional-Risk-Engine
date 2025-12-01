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

from engine.stream import update_covariance_ewma, MockDataStream, LAMBDA_DECAY
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

rm = RiskManager()
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# ==========================================
# 1. HEADER & CONTROLS
# ==========================================
st.title("🛡️ Institutional Risk Engine")

with st.expander("⚙️ Simulation Controls", expanded=True):
    col_ctrl1, col_ctrl2 = st.columns(2)
    with col_ctrl1:
        st.info("Market Data Source: Monte Carlo Simulation")
    with col_ctrl2:
        run_foreground = st.toggle("🟢 Activate Real-Time Simulation", value=True)

st.divider()

# ==========================================
# 2. LIVE MARKET PRICES
# ==========================================
st.subheader("🔴 Live Market Prices")
ticker_cols = st.columns(len(TICKERS))
metric_placeholders = [col.empty() for col in ticker_cols]

st.divider()

# ==========================================
# 3. HORIZONTAL EXECUTION BLOTTER (NEW)
# ==========================================
st.subheader("⚡ Execution Blotter")

# Initialize Session State for Trading
if 'trade_stage' not in st.session_state:
    st.session_state.trade_stage = 'input'
if 'trade_proposal' not in st.session_state:
    st.session_state.trade_proposal = None

def reset_trade():
    st.session_state.trade_stage = 'input'
    st.session_state.trade_proposal = None

with st.container(border=True):
    # --- STAGE 1: INPUT ROW ---
    if st.session_state.trade_stage == 'input':
        c1, c2, c3, c4 = st.columns([1.5, 1.5, 1.5, 2])
        
        with c1:
            ticker = st.selectbox("Ticker", TICKERS, label_visibility="collapsed")
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"], label_visibility="collapsed")
        with c3:
            qty = st.number_input("Qty", min_value=1, value=100, step=10, label_visibility="collapsed")
        with c4:
            if st.button("🔍 Check Risk Impact", type="primary", use_container_width=True):
                impact = rm.check_trade_impact(ticker, qty, side)
                st.session_state.trade_proposal = {
                    "ticker": ticker, "qty": qty, "side": side, "impact": impact
                }
                st.session_state.trade_stage = 'confirm'
                st.rerun()

    # --- STAGE 2: CONFIRMATION ROW ---
    elif st.session_state.trade_stage == 'confirm':
        p = st.session_state.trade_proposal
        i = p['impact']
        
        # Header showing the trade
        st.markdown(f"**Confirm Order:** :blue[{p['side']} {p['qty']} {p['ticker']}]")
        
        if i['status'] == "APPROVED":
            # Metrics Row
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Incremental VaR", f"${i['incremental_var']:,.2f}")
            m2.metric("Liquidity Cost", f"${i['liquidity_cost']:,.2f}")
            m3.metric("New Total VaR", f"${i['post_trade_var']:,.0f}")
            m4.success("✅ RISK APPROVED")
            
            # Action Buttons
            btn_col1, btn_col2 = st.columns([1, 4])
            with btn_col1:
                if st.button("❌ Cancel", use_container_width=True):
                    reset_trade()
                    st.rerun()
            with btn_col2:
                if st.button("🚀 EXECUTE TRADE", type="primary", use_container_width=True):
                    rm.execute_trade(p['ticker'], p['qty'], p['side'])
                    st.toast(f"Trade Executed: {p['side']} {p['qty']} {p['ticker']}", icon="✅")
                    time.sleep(1)
                    reset_trade()
                    st.rerun()
                    
        else:
            # Rejection Row
            st.error(f"❌ BLOCKED: {i.get('reason')}")
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Projected VaR", f"${i.get('post_trade_var', 0):,.0f}")
            k2.metric("Limit", f"${i['limit']:,.0f}")
            
            if st.button("🔙 Go Back"):
                reset_trade()
                st.rerun()

st.divider()

# ==========================================
# 4. ACCOUNT SUMMARY
# ==========================================
st.subheader("🏦 Account Summary")
kpi_cols = st.columns(4)
kpi_placeholders = [col.empty() for col in kpi_cols]

st.divider()

# ==========================================
# 5. TABLES & MATRIX
# ==========================================
col_table, col_matrix = st.columns([1.5, 1])
with col_table:
    st.subheader("💼 Holdings")
    table_placeholder = st.empty()
with col_matrix:
    st.subheader("🧠 Correlations")
    matrix_placeholder = st.empty()

# ==========================================
# 6. SIDEBAR STATUS
# ==========================================
st.sidebar.header("🔌 System Status")
try:
    r = get_redis_connection()
    hb = r.get("stream:heartbeat")
    if hb: st.sidebar.success(f"Online: {hb.decode()}")
except: pass

# ==========================================
# 7. DATA RENDERING HELPER
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
        
        var_color = "normal" if data['port_var'] < data['limit'] else "inverse"
        kpi_placeholders[2].metric("VaR", f"${data['port_var']:,.0f}", f"Limit: ${data['limit']:,.0f}", delta_color=var_color)
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
# 8. EXECUTION LOOP (Frame-by-Frame)
# ==========================================

if run_foreground:
    # --- SIMULATION MODE ---
    
    # 1. Load State
    cov_matrix, prices = rm.get_market_data()
    
    # Auto-Warmup
    if cov_matrix is None:
        with st.spinner("Initializing Database..."):
            run_warmup()
            cov_matrix, prices = rm.get_market_data()
            
    # 2. Generate Tick
    if prices is not None:
        stream = MockDataStream(prices)
        new_prices = stream.get_next_tick()
        returns = np.log(new_prices / prices) 
        new_cov_matrix = update_covariance_ewma(cov_matrix, returns, LAMBDA_DECAY)
        
        # 3. Save
        r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
        price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
        r.set("market_data:last_prices", pickle.dumps(price_dict))
        r.set("stream:heartbeat", datetime.now().strftime("%H:%M:%S"))

    # 4. Render
    render_dashboard()
    
    # 5. Loop via Rerun
    time.sleep(1.5)
    st.rerun()

else:
    # --- PAUSED MODE ---
    render_dashboard()
    st.caption("Enable Simulation above to start data stream.")
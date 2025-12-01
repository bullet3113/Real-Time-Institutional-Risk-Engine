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

rm = RiskManager()
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# ==========================================
# 1. NEW: TRADE DIALOG (MODAL)
# ==========================================
@st.dialog("⚡ Trade Execution Blotter")
def open_trade_blotter():
    # Initialize session state specific to this interaction
    if 'blotter_step' not in st.session_state:
        st.session_state.blotter_step = "input"
    if 'blotter_proposal' not in st.session_state:
        st.session_state.blotter_proposal = None

    # --- STEP 1: INPUT ---
    if st.session_state.blotter_step == "input":
        st.caption("Enter trade details to analyze risk impact.")
        
        col1, col2 = st.columns(2)
        with col1:
            ticker = st.selectbox("Ticker", TICKERS, key="dlg_ticker")
            side = st.selectbox("Side", ["BUY", "SELL"], key="dlg_side")
        with col2:
            qty = st.number_input("Quantity", min_value=1, value=100, step=10, key="dlg_qty")
            st.write("") # Spacer
            st.write("") 
        
        if st.button("🔍 Analyze Risk Impact", use_container_width=True):
            with st.spinner("Calculating Incremental VaR..."):
                impact = rm.check_trade_impact(ticker, qty, side)
                st.session_state.blotter_proposal = {
                    "ticker": ticker, "qty": qty, "side": side, "impact": impact
                }
                st.session_state.blotter_step = "confirm"
                st.rerun()

    # --- STEP 2: CONFIRMATION ---
    elif st.session_state.blotter_step == "confirm":
        p = st.session_state.blotter_proposal
        i = p['impact']
        
        st.subheader(f"{p['side']} {p['qty']} {p['ticker']}")
        
        # Display Risk Metrics
        if i['status'] == "APPROVED":
            st.success("✅ RISK CHECK PASSED")
            
            met1, met2, met3 = st.columns(3)
            met1.metric("Incremental VaR", f"${i['incremental_var']:,.2f}")
            met2.metric("Liquidity Cost", f"${i['liquidity_cost']:,.2f}")
            met3.metric("New Total VaR", f"${i['post_trade_var']:,.0f}")
            
            st.info(f"This trade adds ${i['incremental_var']:,.0f} to your total risk.")
            
            col_exec, col_cancel = st.columns([2, 1])
            if col_exec.button("🚀 CONFIRM EXECUTION", type="primary", use_container_width=True):
                rm.execute_trade(p['ticker'], p['qty'], p['side'])
                st.toast(f"Executed: {p['side']} {p['qty']} {p['ticker']}", icon="✅")
                # Reset and Close
                st.session_state.blotter_step = "input"
                st.session_state.blotter_proposal = None
                st.rerun()
                
            if col_cancel.button("Cancel", use_container_width=True):
                st.session_state.blotter_step = "input"
                st.rerun()
                
        else:
            st.error("❌ TRADE BLOCKED")
            st.markdown(f"**Reason:** {i.get('reason')}")
            
            k1, k2 = st.columns(2)
            k1.metric("Projected VaR", f"${i.get('post_trade_var', 0):,.0f}")
            k2.metric("Limit", f"${i['limit']:,.0f}")
            
            if st.button("🔙 Back to Edit", use_container_width=True):
                st.session_state.blotter_step = "input"
                st.rerun()


# ==========================================
# 2. MAIN LAYOUT
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
# 3. EXECUTION BUTTON (Top of Dashboard)
# ==========================================
col_exec_btn, col_blank = st.columns([1, 4])
with col_exec_btn:
    if st.button("⚡ Execute New Trade", type="primary", use_container_width=True):
        open_trade_blotter()

# ==========================================
# 4. DASHBOARD PLACEHOLDERS
# ==========================================
st.subheader("🔴 Live Market Prices")
ticker_cols = st.columns(len(TICKERS))
metric_placeholders = [col.empty() for col in ticker_cols]

st.divider()

st.subheader("🏦 Account Summary")
kpi_cols = st.columns(4)
kpi_placeholders = [col.empty() for col in kpi_cols]

st.divider()

col_table, col_matrix = st.columns([1.5, 1])
with col_table:
    st.subheader("💼 Holdings")
    table_placeholder = st.empty()
with col_matrix:
    st.subheader("🧠 Correlations")
    matrix_placeholder = st.empty()

# ==========================================
# 5. DATA RENDERING HELPER
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
# 6. SIDEBAR STATUS (Blotter Removed)
# ==========================================
st.sidebar.header("🔌 System Status")
try:
    r = get_redis_connection()
    last_heartbeat = r.get("stream:heartbeat")
    if last_heartbeat:
        st.sidebar.success(f"Stream Online: {last_heartbeat.decode()}")
    else:
        st.sidebar.warning("Stream Starting...")
except:
    pass

# ==========================================
# 7. EXECUTION LOOP
# ==========================================

if run_foreground:
    from engine.stream import update_covariance_ewma, MockDataStream, LAMBDA_DECAY
    
    cov_matrix, prices = rm.get_market_data()
    
    if cov_matrix is None:
        with st.spinner("Initializing Database..."):
            run_warmup()
            cov_matrix, prices = rm.get_market_data()
            
    stream = MockDataStream(prices)
    current_cov_matrix = cov_matrix
    last_prices = prices

    while run_foreground:
        new_prices = stream.get_next_tick()
        returns = np.log(new_prices / last_prices)
        new_cov_matrix = update_covariance_ewma(current_cov_matrix, returns, LAMBDA_DECAY)
        
        r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
        price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
        r.set("market_data:last_prices", pickle.dumps(price_dict))
        r.set("stream:heartbeat", datetime.now().strftime("%H:%M:%S"))
        
        last_prices = new_prices
        current_cov_matrix = new_cov_matrix
        
        render_dashboard()
        time.sleep(1.5)

else:
    render_dashboard()
    st.caption("Enable Simulation above to start data stream.")
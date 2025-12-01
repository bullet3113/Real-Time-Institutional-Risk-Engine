import streamlit as st
import pandas as pd
import numpy as np
import time
import threading
import sys
import os
from datetime import datetime

# Allow imports from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine.stream import run_stream_processor
from engine.warmup import run_warmup
from db_config import get_redis_connection
from logic.risk_manager import RiskManager

st.set_page_config(layout="wide", page_title="Institutional Risk Dashboard")

# --- BACKGROUND PROCESS MANAGER ---
@st.cache_resource
def start_background_processes():
    """Starts the stream in a background thread ONCE."""
    try:
        r = get_redis_connection()
        if not r.exists("portfolio:cash"):
            print("Running Warmup...")
            run_warmup()
            
        print("Starting Stream Thread...")
        t = threading.Thread(target=run_stream_processor, daemon=True)
        t.start()
        return t
    except Exception as e:
        print(f"Background Process Failed: {e}")
        return None

# Run the loader
start_background_processes()

# Initialize Logic
rm = RiskManager()
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# ==========================================
# 1. HEADER & LIVE STATUS
# ==========================================
col_title, col_time = st.columns([3, 1])
col_title.markdown("## 🛡️ Real-Time Risk Engine")
# Visual Proof that the page is refreshing
col_time.caption(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")

# ==========================================
# 2. LIVE PRICES (TOP ROW)
# ==========================================
st.markdown("### 🔴 Live Market Prices")
cov_matrix, prices = rm.get_market_data()

# Render Prices if available
if prices is not None:
    cols = st.columns(len(TICKERS))
    for i, ticker in enumerate(TICKERS):
        cols[i].metric(label=ticker, value=f"${prices[i]:.2f}")
else:
    st.warning("Waiting for Market Data Stream...")

st.divider()

# ==========================================
# 3. KPIS & ACCOUNT
# ==========================================
st.markdown("### 🏦 Account Summary")
data = rm.get_dashboard_metrics()

if data:
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Net Liquidation", f"${data['total_value']:,.0f}")
    equity_val = data['total_value'] - data['cash']
    k2.metric("Stock Equity", f"${equity_val:,.0f}")
    k3.metric("Available Cash", f"${data['cash']:,.0f}")
    
    var_color = "normal" if data['port_var'] < data['limit'] else "inverse"
    k4.metric("Portfolio VaR", f"${data['port_var']:,.0f}", 
              f"Limit: ${data['limit']:,.0f}", delta_color=var_color)
    k5.metric("Daily Volatility", f"{data['port_std_daily']*100:.2f}%")

st.divider()

# ==========================================
# 4. FILTERED TABLE & MATRIX
# ==========================================
col_table, col_matrix = st.columns([1.5, 1])

with col_table:
    st.markdown("### 💼 Holdings")
    if data:
        df = data['table_data']
        active_df = df[df['Qty'] > 0].copy()
        
        if not active_df.empty:
            st.dataframe(
                active_df.style
                .background_gradient(subset=['Risk Contrib ($)'], cmap="Reds")
                .format({
                    "Price": "${:,.2f}",
                    "Avg Buy Price": "${:,.2f}",
                    "Invested": "${:,.0f}",
                    "Current Value": "${:,.0f}",
                    "Weight (%)": "{:.1f}%",
                    "Daily Volatility": "{:.2f}%",
                    "Isolated VaR": "${:,.0f}",
                    "Risk Contrib ($)": "${:,.0f}"
                }),
                width="stretch",
                height=300
            )
        else:
            st.info("No Active Positions. Use Sidebar to Trade.")

with col_matrix:
    st.markdown("### 🧠 Correlations")
    if cov_matrix is not None:
        std_devs = np.sqrt(np.diagonal(cov_matrix))
        std_devs[std_devs == 0] = 1e-9 
        outer_vols = np.outer(std_devs, std_devs)
        corr_matrix = cov_matrix / outer_vols
        corr_df = pd.DataFrame(corr_matrix, index=TICKERS, columns=TICKERS)
        
        st.dataframe(
            corr_df.style.background_gradient(cmap="RdYlGn_r", vmin=-1, vmax=1).format("{:.2f}"),
            width="stretch"
        )

# ==========================================
# SIDEBAR: STATUS & BLOTTER
# ==========================================
st.sidebar.header("🔌 System Status")

# Fetch Status Safely
try:
    r = get_redis_connection()
    last_heartbeat = r.get("stream:heartbeat")
    stream_error = r.get("stream:error")
except:
    last_heartbeat = None
    stream_error = None

if last_heartbeat:
    st.sidebar.success(f"Stream Online: {last_heartbeat.decode()}")
else:
    st.sidebar.warning("Stream Starting...")

if stream_error:
    st.sidebar.error(f"Error: {stream_error.decode()}")

st.sidebar.header("⚡ Execution Blotter")

if 'trade_stage' not in st.session_state:
    st.session_state.trade_stage = 'input'

def reset_trade():
    st.session_state.trade_stage = 'input'
    st.session_state.trade_proposal = None

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
            st.success("Trade Executed!")
            time.sleep(0.5)
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
# CRITICAL: THE AUTO-REFRESH MECHANISM
# ==========================================
time.sleep(1)  # Refresh Rate
st.rerun()     # Force Streamlit to re-run the whole script
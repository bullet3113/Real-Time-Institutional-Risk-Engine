import streamlit as st
import pandas as pd
import numpy as np
import time
import threading
import sys
import os

# Allow imports from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine.stream import run_stream_processor
from engine.warmup import run_warmup
from db_config import get_redis_connection
from logic.risk_manager import RiskManager

st.set_page_config(layout="wide", page_title="Institutional Risk Dashboard")

# --- BACKGROUND PROCESS MANAGER (CLOUD COMPATIBLE) ---
@st.cache_resource
def start_background_processes():
    """Starts the stream in a background thread ONCE."""
    r = get_redis_connection()
    
    # Check if DB needs warmup
    if not r.exists("portfolio:cash"):
        print("Running Warmup...")
        run_warmup()

    print("Starting Stream Thread...")
    t = threading.Thread(target=run_stream_processor, daemon=True)
    t.start()
    return t

# Run the loader
start_background_processes()

# Initialize Logic
rm = RiskManager()
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# ==========================================
# 1. LIVE PRICE TICKER (TOP)
# ==========================================
st.markdown("### 🔴 Live Market Prices")
price_container = st.empty()
st.divider()

# ==========================================
# 2. ACCOUNT SUMMARY (KPIs)
# ==========================================
st.markdown("### 🏦 Account Summary")
kpi_container = st.empty()
st.divider()

# ==========================================
# 3. PORTFOLIO HOLDINGS (Filtered)
# ==========================================
st.markdown("### 💼 Portfolio Holdings (Mark-to-Market)")
table_container = st.empty()
st.divider()

# ==========================================
# 4. RISK MATRIX (Correlation)
# ==========================================
st.markdown("### 🧠 Real-Time Correlation Matrix")
matrix_container = st.empty()

# ==========================================
# SIDEBAR: SYSTEM STATUS & EXECUTION
# ==========================================
st.sidebar.header("🔌 System Status")
status_container = st.sidebar.empty()
error_container = st.sidebar.empty()

# --- FIX: Fetch Data BEFORE checking it ---
try:
    r = get_redis_connection()
    last_heartbeat = r.get("stream:heartbeat")
    stream_error = r.get("stream:error")
except Exception as e:
    last_heartbeat = None
    stream_error = f"Redis Connection Error: {str(e)}".encode()

# 1. Show Heartbeat
if last_heartbeat:
    status_container.success(f"Stream Online: {last_heartbeat.decode()}")
else:
    status_container.warning("Stream Starting / Waiting...")

# 2. Show Background Errors (Now safely defined)
if stream_error:
    error_container.error(f"Thread Error: {stream_error.decode()}")

st.sidebar.header("⚡ Execution Blotter")

if 'trade_stage' not in st.session_state:
    st.session_state.trade_stage = 'input'

def reset_trade():
    st.session_state.trade_stage = 'input'
    st.session_state.trade_proposal = None

with st.sidebar:
    if st.session_state.trade_stage == 'input':
        with st.form("trade_input"):
            ticker = st.selectbox("Ticker", TICKERS)
            side = st.selectbox("Side", ["BUY", "SELL"])
            qty = st.number_input("Quantity", min_value=1, value=100)
            
            if st.form_submit_button("Check Risk"):
                impact = rm.check_trade_impact(ticker, qty, side)
                st.session_state.trade_proposal = {
                    "ticker": ticker, "qty": qty, "side": side, "impact": impact
                }
                st.session_state.trade_stage = 'confirm'
                st.rerun()

    elif st.session_state.trade_stage == 'confirm':
        proposal = st.session_state.trade_proposal
        impact = proposal['impact']
        
        st.info(f"Confirm {proposal['side']} {proposal['qty']} {proposal['ticker']}?")
        
        if impact['status'] == "APPROVED":
            st.success("✅ RISK CHECK PASSED")
            st.write(f"**Incremental VaR:** ${impact['incremental_var']:.2f}")
            st.write(f"**Liq. Cost:** ${impact['liquidity_cost']:.2f}")
            
            col_a, col_b = st.columns(2)
            if col_a.button("EXECUTE"):
                success = rm.execute_trade(proposal['ticker'], proposal['qty'], proposal['side'])
                if success:
                    st.success("Executed!")
                    time.sleep(1)
                    reset_trade()
                    st.rerun()
                else:
                    st.error("Execution Failed (Check Logs)")
            
            if col_b.button("CANCEL"):
                reset_trade()
                st.rerun()
        else:
            st.error("❌ TRADE BLOCKED")
            st.write(f"Reason: {impact.get('reason')}")
            if st.button("Back"):
                reset_trade()
                st.rerun()

# ==========================================
# MAIN AUTO-REFRESH LOOP
# ==========================================
while True:
    # Fetch all data from Redis via RiskManager
    data = rm.get_dashboard_metrics()
    cov_matrix, prices = rm.get_market_data()
    
    if data and cov_matrix is not None:
        
        # --- SECTION 1: LIVE PRICES ---
        with price_container.container():
            cols = st.columns(len(TICKERS))
            for i, ticker in enumerate(TICKERS):
                cols[i].metric(label=ticker, value=f"${prices[i]:.2f}")

        # --- SECTION 2: KPIS ---
        with kpi_container.container():
            k1, k2, k3, k4, k5 = st.columns(5)
            
            k1.metric("Net Liquidation Value", f"${data['total_value']:,.0f}")
            
            equity_val = data['total_value'] - data['cash']
            k2.metric("Stock Equity (MtM)", f"${equity_val:,.0f}")
            
            k3.metric("Available Cash", f"${data['cash']:,.0f}")
            
            var_color = "normal" if data['port_var'] < data['limit'] else "inverse"
            k4.metric("Portfolio VaR (95%)", f"${data['port_var']:,.0f}", 
                      f"Limit: ${data['limit']:,.0f}", delta_color=var_color)

            k5.metric("Portfolio Vol (Daily)", f"{data['port_std_daily']*100:.2f}%")

        # --- SECTION 3: FILTERED TABLE ---
        with table_container.container():
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
                st.info("No stocks in portfolio. Use the blotter to trade.")

        # --- SECTION 4: COVARIANCE MATRIX ---
        with matrix_container.container():
            std_devs = np.sqrt(np.diagonal(cov_matrix))
            std_devs[std_devs == 0] = 1e-9 
            
            outer_vols = np.outer(std_devs, std_devs)
            corr_matrix = cov_matrix / outer_vols
            
            corr_df = pd.DataFrame(corr_matrix, index=TICKERS, columns=TICKERS)
            
            st.dataframe(
                corr_df.style.background_gradient(cmap="RdYlGn_r", vmin=-1, vmax=1).format("{:.2f}"),
                width="stretch"
            )

    time.sleep(1)
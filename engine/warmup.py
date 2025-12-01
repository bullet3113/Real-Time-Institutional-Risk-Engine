import numpy as np
import pandas as pd
import yfinance as yf
import redis
import pickle
import sys
from db_config import get_redis_connection

# ==========================================
# CONFIGURATION
# ==========================================
# Make sure these match your config.py
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]

# Redis Connection
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0

# Timeframes
# 1. Current Warmup: Max 7 days for 1m interval on yfinance
CURRENT_PERIOD = "5d" 
CURRENT_INTERVAL = "1m"

# 2. Stressed Regime: The COVID Crash (High Volatility)
STRESSED_START = "2020-02-20"
STRESSED_END = "2020-03-25"

def connect_redis():
    try:
        r = get_redis_connection()
        r.ping()
        print(f"[SUCCESS] Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
        return r
    except redis.ConnectionError:
        print("[ERROR] Could not connect to Redis. Is the server running?")
        sys.exit(1)

def get_log_returns(data):
    """Computes Log Returns: ln(P_t / P_t-1)"""
    # Forward fill to handle small gaps, then drop remaining NaNs
    data = data.ffill().dropna()
    returns = np.log(data / data.shift(1)).dropna()
    return returns

def run_warmup():
    r = connect_redis()
    print(f"--- Starting Warmup for {len(TICKERS)} tickers: {TICKERS} ---")

    # ==================================================
    # STEP 1: COMPUTE STRESSED MATRIX (The Safety Net)
    # ==================================================
    print("\n[1/3] Fetching Historical Crisis Data (Stressed Regime)...")
    
    # We use Daily data for history because 1-min data from 2020 is not free.
    stressed_data = yf.download(TICKERS, start=STRESSED_START, end=STRESSED_END, progress=False)['Close']
    
    stressed_returns = get_log_returns(stressed_data)
    stressed_cov_daily = stressed_returns.cov().values
    
    # CRITICAL: Scale Daily Variance to 1-Minute Variance
    # There are approx 390 trading minutes in a day (6.5 hours * 60)
    # Variance scales linearly with time.
    minutes_per_day = 390
    stressed_cov_1min = stressed_cov_daily / minutes_per_day
    
    print(f"      Calculated Stressed Matrix (Scaled to 1-min). Shape: {stressed_cov_1min.shape}")

    # ==================================================
    # STEP 2: COMPUTE CURRENT MATRIX (The Baseline)
    # ==================================================
    print("\n[2/3] Fetching Recent 1-Minute Data (Current Regime)...")
    
    # Fetch 1-minute data to seed the EWMA model correctly
    current_data = yf.download(TICKERS, period=CURRENT_PERIOD, interval=CURRENT_INTERVAL, progress=False)['Close']
    
    # Compute returns
    current_returns = get_log_returns(current_data)
    
    # Calculate standard covariance as the starting point for EWMA
    current_cov_matrix = current_returns.cov().values
    
    # Get the very last prices (to initialize Returns calculation in the live stream)
    last_prices = current_data.iloc[-1].values
    
    print(f"      Calculated Current Matrix based on last {CURRENT_PERIOD}. Shape: {current_cov_matrix.shape}")
    print(f"      Captured Last Prices: {last_prices}")

    # ==================================================
    # STEP 3: SAVE TO REDIS
    # ==================================================
    print("\n[3/3] Saving State to Redis...")

    # We use Pickle to serialize the NumPy arrays
    # 1. Stressed Matrix (Static - won't change)
    r.set("risk:cov_matrix:stressed", pickle.dumps(stressed_cov_1min))
    
    # 2. Current Matrix (Dynamic - will be updated by stream.py)
    r.set("risk:cov_matrix:current", pickle.dumps(current_cov_matrix))
    
    # 3. Last Prices (Needed for the first tick in stream.py)
    # Store as a dictionary for easier lookup: {'AAPL': 150.0, ...}
    price_dict = {ticker: price for ticker, price in zip(TICKERS, last_prices)}
    r.set("market_data:last_prices", pickle.dumps(price_dict))
    
    # 4. Ticker List (For validation)
    r.set("config:tickers", pickle.dumps(TICKERS))

    print("\n[DONE] Warmup Complete. Redis is seeded and ready for the Data Stream.")
    
    # ... (Previous code for downloading market data) ...

    # ==================================================
    # STEP 4: INITIALIZE PORTFOLIO (NEW)
    # ==================================================
    print("\n[4/4] Initializing Portfolio State...")
    
    # 1. Cash: $1,000,000
    r.set("portfolio:cash", 1_000_000.0)
    
    # 2. Holdings: Empty Dictionary
    # Structure: {'AAPL': {'qty': 0, 'avg_price': 0.0}, ...}
    initial_holdings = {t: {'qty': 0, 'avg_price': 0.0} for t in TICKERS}
    r.set("portfolio:holdings", pickle.dumps(initial_holdings))
    
    print(f"      Funded Account: $1,000,000")
    print(f"      Positions: Cleared")

    print("\n[DONE] Warmup Complete.")

if __name__ == "__main__":
    run_warmup()
    

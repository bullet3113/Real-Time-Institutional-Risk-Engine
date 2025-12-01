import time
import pickle
import numpy as np
import threading
import sys
import os
from datetime import datetime

# Allow imports from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# IMPORT THE CENTRAL DB CONNECTION
from db_config import get_redis_connection

TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]
LAMBDA_DECAY = 0.94
REFRESH_RATE_SEC = 2  # 2 seconds sleep

def update_covariance_ewma(old_matrix, current_returns, decay):
    r_t = current_returns.reshape(-1, 1)
    shock_matrix = np.dot(r_t, r_t.T)
    new_matrix = (decay * old_matrix) + ((1 - decay) * shock_matrix)
    return new_matrix

class MockDataStream:
    def __init__(self, initial_prices):
        self.prices = initial_prices
        
    def get_next_tick(self):
        # Generate random returns between -0.2% and +0.2%
        # Increased volatility slightly so you can see changes clearly
        returns = np.random.normal(0, 0.002, len(self.prices))
        self.prices = self.prices * (1 + returns)
        return self.prices

def run_stream_processor():
    print("[STREAM] Starting Thread...")
    
    # 1. Connect using central config (Critical for Cloud)
    try:
        r = get_redis_connection()
        r.ping()
        print(f"[STREAM] Connected to Redis.")
    except Exception as e:
        print(f"[STREAM] Redis Connection Failed: {e}")
        return

    # 2. Load Initial State
    try:
        cov_matrix_bytes = r.get("risk:cov_matrix:current")
        prices_bytes = r.get("market_data:last_prices")
        
        if not cov_matrix_bytes or not prices_bytes:
            print("[STREAM] Data missing. Waiting for Warmup...")
            return
            
        current_cov_matrix = pickle.loads(cov_matrix_bytes)
        last_prices_dict = pickle.loads(prices_bytes)
        last_prices = np.array([last_prices_dict[t] for t in TICKERS])
        
    except Exception as e:
        print(f"[STREAM] Initialization Error: {e}")
        return

    stream = MockDataStream(last_prices)

    # 3. Infinite Loop
    while True:
        try:
            # A. Generate Data
            new_prices = stream.get_next_tick()
            returns = np.log(new_prices / last_prices)
            
            # B. Math
            new_cov_matrix = update_covariance_ewma(current_cov_matrix, returns, LAMBDA_DECAY)
            
            # C. Save to Redis
            r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
            
            price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
            r.set("market_data:last_prices", pickle.dumps(price_dict))
            
            # --- D. THE HEARTBEAT (New) ---
            # Write the current time so UI knows we are alive
            current_time = datetime.now().strftime("%H:%M:%S")
            r.set("stream:heartbeat", current_time)
            
            # E. Logging (Check Streamlit Logs)
            # print(f"[STREAM] {current_time} Updated. AAPL: {new_prices[0]:.2f}")

            # F. Prepare Next Loop
            last_prices = new_prices
            current_cov_matrix = new_cov_matrix
            
            time.sleep(REFRESH_RATE_SEC)
            
        except Exception as e:
            print(f"[STREAM] Loop Crash: {e}")
            time.sleep(5) # Wait before retrying

if __name__ == "__main__":
    run_stream_processor()
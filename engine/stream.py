import time
import redis
import pickle
import numpy as np
import pandas as pd
import random
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0

TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]
LAMBDA_DECAY = 0.94  # The RiskMetrics standard

# Simulation Settings
REFRESH_RATE_SEC = 2  # Speed up time (Update every 2s instead of 60s for demo)

# ==========================================
# HELPER: EWMA UPDATE LOGIC
# ==========================================
def update_covariance_ewma(old_matrix, current_returns, decay):
    """
    The Core Recursive Formula:
    New_Cov = (Decay * Old_Cov) + ((1-Decay) * Returns * Returns.T)
    """
    # 1. Reshape returns to column vector (N, 1)
    r_t = current_returns.reshape(-1, 1)
    
    # 2. Outer Product (The "Shock" of the current moment)
    shock_matrix = np.dot(r_t, r_t.T)
    
    # 3. Weighted Sum
    new_matrix = (decay * old_matrix) + ((1 - decay) * shock_matrix)
    
    return new_matrix

# ==========================================
# MOCK DATA SOURCE (Replace with Alpaca/Polygon)
# ==========================================
class MockDataStream:
    """Simulates a WebSocket connection receiving 1-minute bars."""
    def __init__(self, initial_prices):
        self.prices = initial_prices
        
    def get_next_tick(self):
        """Generates random market moves."""
        # Random return between -0.5% and +0.5%
        returns = np.random.normal(0, 0.005, len(self.prices))
        
        # Apply returns to prices
        self.prices = self.prices * (1 + returns)
        return self.prices

# ==========================================
# MAIN LOOP
# ==========================================
def run_stream_processor():
    # 1. Connect to Redis
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    print(f"[STREAM] Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")

    # 2. Load Initial State (From Warmup)
    try:
        cov_matrix_bytes = r.get("risk:cov_matrix:current")
        prices_bytes = r.get("market_data:last_prices")
        
        if not cov_matrix_bytes or not prices_bytes:
            raise ValueError("Data missing in Redis. Did you run 'warmup.py'?")
            
        current_cov_matrix = pickle.loads(cov_matrix_bytes)
        last_prices_dict = pickle.loads(prices_bytes)
        
        # Convert dict back to numpy array in correct order of TICKERS
        last_prices = np.array([last_prices_dict[t] for t in TICKERS])
        
        print(f"[STREAM] State Loaded. Starting Engine for: {TICKERS}")
        
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    # 3. Initialize Data Source
    stream = MockDataStream(last_prices)

    # 4. Infinite Loop
    print(f"[STREAM] Listening for ticks... (Simulated every {REFRESH_RATE_SEC}s)")
    
    while True:
        try:
            # --- A. Ingest Data ---
            new_prices = stream.get_next_tick()
            
            # --- B. Calculate Returns ---
            # Log Return = ln(New / Old)
            # Add small epsilon to avoid divide by zero if price is 0 (unlikely)
            returns = np.log(new_prices / last_prices)
            
            # --- C. Update Matrix (EWMA) ---
            new_cov_matrix = update_covariance_ewma(current_cov_matrix, returns, LAMBDA_DECAY)
            
            # --- D. Save to Redis ---
            # 1. Save Matrix
            r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
            
            # 2. Save Prices (As Dict for Dashboard)
            price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
            r.set("market_data:last_prices", pickle.dumps(price_dict))
            
            # --- E. Logging ---
            # We calculate Volatility just for the console log
            current_vols = np.sqrt(np.diagonal(new_cov_matrix))
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            print(f"[{timestamp}] Matrix Updated | AAPL: ${new_prices[0]:.2f} | Vol: {current_vols[0]:.4f}")
            
            # --- F. Prepare for Next Loop ---
            last_prices = new_prices
            current_cov_matrix = new_cov_matrix
            
            time.sleep(REFRESH_RATE_SEC)
            
        except KeyboardInterrupt:
            print("\n[STREAM] Stopping...")
            break
        except Exception as e:
            print(f"[ERROR] Loop Failed: {e}")
            time.sleep(1)

if __name__ == "__main__":
    run_stream_processor()
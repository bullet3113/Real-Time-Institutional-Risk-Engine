import time
import pickle
import numpy as np
import sys
import os
import traceback
from datetime import datetime

# Allow imports from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from db_config import get_redis_connection

TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]
LAMBDA_DECAY = 0.94
REFRESH_RATE_SEC = 2

def update_covariance_ewma(old_matrix, current_returns, decay):
    r_t = current_returns.reshape(-1, 1)
    shock_matrix = np.dot(r_t, r_t.T)
    new_matrix = (decay * old_matrix) + ((1 - decay) * shock_matrix)
    return new_matrix

class MockDataStream:
    def __init__(self, initial_prices):
        self.prices = initial_prices
        
    def get_next_tick(self):
        returns = np.random.normal(0, 0.002, len(self.prices))
        self.prices = self.prices * (1 + returns)
        return self.prices

def run_stream_processor():
    # 1. Connect
    try:
        r = get_redis_connection()
        r.set("stream:status", "Initializing...")
    except Exception as e:
        print(f"Redis Connection Failed: {e}")
        return

    # 2. WAIT LOOP: Wait for Warmup to finish
    current_cov_matrix = None
    last_prices = None
    
    while current_cov_matrix is None:
        try:
            cov_matrix_bytes = r.get("risk:cov_matrix:current")
            prices_bytes = r.get("market_data:last_prices")
            
            if cov_matrix_bytes and prices_bytes:
                current_cov_matrix = pickle.loads(cov_matrix_bytes)
                last_prices_dict = pickle.loads(prices_bytes)
                last_prices = np.array([last_prices_dict[t] for t in TICKERS])
                r.set("stream:status", "Data Loaded. Starting Stream.")
            else:
                # DATA MISSING? WRITE LOG AND WAIT
                r.set("stream:error", "Waiting for Warmup Data...")
                time.sleep(5) # Wait 5 seconds and try again
        except Exception as e:
            r.set("stream:error", f"Init Error: {str(e)}")
            time.sleep(5)

    # 3. Start Stream
    stream = MockDataStream(last_prices)
    
    while True:
        try:
            # Clear previous errors if running
            r.delete("stream:error")
            
            new_prices = stream.get_next_tick()
            returns = np.log(new_prices / last_prices)
            
            new_cov_matrix = update_covariance_ewma(current_cov_matrix, returns, LAMBDA_DECAY)
            
            # Save
            r.set("risk:cov_matrix:current", pickle.dumps(new_cov_matrix))
            price_dict = {t: p for t, p in zip(TICKERS, new_prices)}
            r.set("market_data:last_prices", pickle.dumps(price_dict))
            
            # Heartbeat
            current_time = datetime.now().strftime("%H:%M:%S")
            r.set("stream:heartbeat", current_time)
            
            last_prices = new_prices
            current_cov_matrix = new_cov_matrix
            
            time.sleep(REFRESH_RATE_SEC)
            
        except Exception as e:
            # CAPTURE CRASHES TO REDIS
            error_msg = f"Loop Crash: {str(e)} \n {traceback.format_exc()}"
            r.set("stream:error", error_msg)
            time.sleep(5)

if __name__ == "__main__":
    run_stream_processor()
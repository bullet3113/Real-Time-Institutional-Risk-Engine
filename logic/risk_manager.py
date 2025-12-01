import numpy as np
import redis
import pickle
import pandas as pd
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from engine.math_logic import (
    calculate_incremental_var, 
    calculate_liquidity_var, 
    calculate_marginal_var, 
    get_portfolio_var
)

REDIS_HOST = 'localhost'
REDIS_PORT = 6379
TICKERS = ["AAPL", "GOOG", "MSFT", "AMZN", "TSLA"]
VAR_LIMIT_DOLLARS = 1_000_000 * 0.005  # $5,000

class RiskManager:
    def __init__(self):
        self.r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)

    def get_market_data(self):
        """Fetch Matrix and Prices with Error Logging"""
        try:
            matrix_bytes = self.r.get("risk:cov_matrix:current")
            prices_bytes = self.r.get("market_data:last_prices")
            
            if not matrix_bytes or not prices_bytes: 
                return None, None
            
            cov_matrix = pickle.loads(matrix_bytes)
            prices_dict = pickle.loads(prices_bytes)
            
            # Ensure proper array shape/order
            current_prices = np.array([float(prices_dict.get(t, 0.0)) for t in TICKERS])
            return cov_matrix, current_prices
        except Exception as e:
            print(f"[ERROR] Market Data Fetch Failed: {e}")
            return None, None

    def get_portfolio_state(self):
        """Fetch Cash and Holdings with Safety Checks"""
        try:
            # 1. Fetch Cash
            cash_val = self.r.get("portfolio:cash")
            if cash_val is None:
                print("[WARN] Cash key missing in Redis.")
                return None, None
            
            cash = float(cash_val)

            # 2. Fetch Holdings
            holdings_bytes = self.r.get("portfolio:holdings")
            if not holdings_bytes:
                print("[WARN] Holdings key missing in Redis.")
                return None, None
                
            holdings = pickle.loads(holdings_bytes)
            return cash, holdings
            
        except Exception as e:
            print(f"[ERROR] Portfolio State Fetch Failed: {e}")
            # RETURN NONE to indicate failure, do NOT return 0.0
            return None, None

    def get_dashboard_metrics(self):
        cov_matrix, prices = self.get_market_data()
        cash, holdings = self.get_portfolio_state()
        
        if cov_matrix is None or cash is None or holdings is None:
            return None

        # Safe Extraction
        quantities = []
        avg_prices = []
        for t in TICKERS:
            data = holdings.get(t, {'qty': 0, 'avg_price': 0.0})
            quantities.append(data['qty'])
            avg_prices.append(data['avg_price'])
            
        quantities = np.array(quantities)
        avg_prices = np.array(avg_prices)
        
        market_values = quantities * prices
        equity_value = np.sum(market_values)
        total_portfolio_value = cash + equity_value
        
        # 1. Weights
        if equity_value > 0:
            weights = market_values / equity_value
        else:
            weights = np.zeros(len(TICKERS))

        # 2. Portfolio Risk Calculation
        # Scaling Factor: Convert 1-min Vol -> Daily Vol (sqrt(390))
        DAILY_FACTOR = np.sqrt(390)

        if equity_value > 0:
            # port_std here is 1-minute volatility
            port_var, port_std_1min = get_portfolio_var(weights, cov_matrix, equity_value)
            
            # CONVERT TO DAILY
            port_std_daily = port_std_1min * DAILY_FACTOR
            
            # Component VaR Logic
            risk_gradient = np.dot(cov_matrix, weights)
            mvar_ratio = risk_gradient / port_std_1min if port_std_1min > 0 else np.zeros_like(weights)
            component_vars = mvar_ratio * weights * equity_value * 1.65
        else:
            port_var = 0.0
            port_std_daily = 0.0
            component_vars = np.zeros(len(TICKERS))

        # 3. Individual Stock Volatility (Convert 1-min to Daily)
        raw_vols_1min = np.sqrt(np.diagonal(cov_matrix))
        daily_vols = raw_vols_1min * DAILY_FACTOR
        
        # Isolated VaR
        isolated_vars = raw_vols_1min * market_values * 1.65

        # 4. Build DataFrame
        data = []
        for i, t in enumerate(TICKERS):
            data.append({
                "Ticker": t,
                "Price": prices[i],
                "Qty": int(quantities[i]),
                "Avg Buy Price": avg_prices[i],
                "Invested": quantities[i] * avg_prices[i],
                "Current Value": market_values[i],
                "Weight (%)": (market_values[i] / total_portfolio_value) * 100 if total_portfolio_value > 0 else 0,
                
                # SHOW DAILY VOLATILITY
                "Daily Volatility": daily_vols[i] * 100, 
                
                "Isolated VaR": isolated_vars[i],
                "Risk Contrib ($)": component_vars[i] 
            })

        return {
            "cash": cash,
            "total_value": total_portfolio_value,
            "port_var": port_var,
            "port_std_daily": port_std_daily, # Sending Daily Scaled Vol
            "limit": VAR_LIMIT_DOLLARS,
            "table_data": pd.DataFrame(data)
        }
    
    def check_trade_impact(self, ticker, qty, side):
        metrics = self.get_dashboard_metrics()
        cov_matrix, prices = self.get_market_data()
        
        if metrics is None: 
            return {"status": "ERROR", "reason": "Data Unavailable"}

        idx = TICKERS.index(ticker)
        current_price = prices[idx]
        trade_value = qty * current_price

        # Funds Check
        if side == "BUY" and trade_value > metrics['cash']:
            return {"status": "REJECTED", "reason": f"Insufficient Funds. Need ${trade_value:,.0f}"}
        
        # Simulating Post-Trade Var
        current_holdings = metrics['table_data'].set_index("Ticker")['Qty'].to_dict()
        current_quantities = np.array([current_holdings.get(t, 0) for t in TICKERS])
        
        trade_vector = np.zeros(len(TICKERS))
        trade_vector[idx] = qty if side == "BUY" else -qty
        
        new_quantities = current_quantities + trade_vector
        if np.any(new_quantities < 0):
             return {"status": "REJECTED", "reason": "Cannot sell more than you own"}

        new_equity_value = np.sum(new_quantities * prices)
        
        if new_equity_value > 0:
            new_weights = (new_quantities * prices) / new_equity_value
            new_var, _ = get_portfolio_var(new_weights, cov_matrix, new_equity_value)
        else:
            new_var = 0.0

        incremental_var = new_var - metrics['port_var']
        
        # Liquidity Check
        bid, ask = current_price * 0.9998, current_price * 1.0002
        liq_var = calculate_liquidity_var(qty, current_price, bid, ask, 10_000_000)

        is_safe = new_var < VAR_LIMIT_DOLLARS
        
        return {
            "status": "APPROVED" if is_safe else "REJECTED",
            "incremental_var": incremental_var,
            "post_trade_var": new_var,
            "liquidity_cost": liq_var,
            "limit": VAR_LIMIT_DOLLARS,
            "trade_value": trade_value
        }

    def execute_trade(self, ticker, qty, side):
        """Executes trade with Double-Check on State Validity"""
        # 1. Fetch State (Stop if invalid)
        cash, holdings = self.get_portfolio_state()
        if cash is None or holdings is None:
            print("[ERROR] Cannot execute trade. Portfolio state is invalid.")
            return False
            
        _, prices = self.get_market_data()
        if prices is None:
            print("[ERROR] Cannot execute trade. Market prices unavailable.")
            return False
        
        # 2. Type Casting (Crucial)
        qty = int(qty)
        idx = TICKERS.index(ticker)
        price = float(prices[idx])
        trade_cost = qty * price
        
        # 3. Double Check Funds Logic
        if side == "BUY":
            if trade_cost > cash:
                print(f"[ERROR] Trade cost ${trade_cost} exceeds cash ${cash}")
                return False
                
            current_qty = int(holdings[ticker]['qty'])
            current_avg = float(holdings[ticker]['avg_price'])
            
            # Calc New Average Price
            total_invested = (current_qty * current_avg) + trade_cost
            new_qty = current_qty + qty
            new_avg = total_invested / new_qty if new_qty > 0 else 0.0
            
            # Deduct Cash
            cash -= trade_cost
            
            # Update Holdings
            holdings[ticker]['qty'] = new_qty
            holdings[ticker]['avg_price'] = new_avg
            
        elif side == "SELL":
            current_qty = int(holdings[ticker]['qty'])
            if qty > current_qty:
                return False
                
            # Add Cash
            cash += trade_cost
            
            # Update Holdings (Avg Price doesn't change on sell)
            holdings[ticker]['qty'] = current_qty - qty
            if holdings[ticker]['qty'] == 0:
                holdings[ticker]['avg_price'] = 0.0

        # 4. Save State
        try:
            self.r.set("portfolio:cash", cash)
            self.r.set("portfolio:holdings", pickle.dumps(holdings))
            print(f"[SUCCESS] Trade Executed. New Cash: ${cash:,.2f}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save trade to Redis: {e}")
            return False
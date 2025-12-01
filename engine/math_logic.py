import numpy as np

# ==========================================
# CONSTANTS
# ==========================================
CONFIDENCE_LEVEL = 1.65  # 95% Confidence (One-sided Z-score)
# Impact Constant: Determines how much price moves per unit of volume
# 0.1 is a conservative default for liquid US equities
MARKET_IMPACT_K = 0.1    

def get_portfolio_var(weights, cov_matrix, portfolio_value):
    """
    Calculates the standard Portfolio Value at Risk.
    Formula: VaR = Z * Portfolio_Value * sqrt(w.T * Sigma * w)
    """
    # 1. Calculate Portfolio Variance (Scalar)
    # w.T (1xN) * Sigma (NxN) * w (Nx1)
    port_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
    
    # 2. Standard Deviation
    port_std = np.sqrt(port_variance)
    
    # 3. Dollar VaR
    var_dollar = port_std * CONFIDENCE_LEVEL * portfolio_value
    
    return var_dollar, port_std

def calculate_marginal_var(weights, cov_matrix, portfolio_value, current_port_std=None):
    """
    Calculates Marginal VaR for ALL assets in the portfolio.
    Interpretation: "If I add $1 to asset i, how much does Portfolio VaR rise?"
    
    Formula: MVaR = (Sigma * w) / Portfolio_Std
    """
    # Avoid re-calculating Std if provided
    if current_port_std is None:
        port_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
        current_port_std = np.sqrt(port_variance)
        
    # Safety check for zero volatility
    if current_port_std == 0:
        return np.zeros_like(weights)

    # Gradient Calculation: Covariance Matrix * Weights Vector
    # Result is a vector of shape (N,)
    risk_gradient = np.dot(cov_matrix, weights)
    
    # Normalize by Portfolio StdDev
    marginal_var_percent = risk_gradient / current_port_std
    
    # Convert to Dollar terms (scaled by Z-score)
    marginal_var_dollars = marginal_var_percent * CONFIDENCE_LEVEL
    
    return marginal_var_dollars

def calculate_incremental_var(current_weights, trade_weight_delta, cov_matrix, portfolio_value):
    """
    Calculates the EXACT change in VaR due to a specific trade.
    
    Step 1: Calculate VaR_old
    Step 2: Calculate VaR_new (with new weights)
    Step 3: Diff
    """
    # 1. Pre-Trade VaR
    var_old, _ = get_portfolio_var(current_weights, cov_matrix, portfolio_value)
    
    # 2. Post-Trade Weights
    # Note: In a real system, you might re-normalize weights to sum to 1,
    # but for risk limits, we often look at raw notional exposure changes.
    new_weights = current_weights + trade_weight_delta
    
    # 3. Post-Trade VaR
    # We increase portfolio value by the trade amount? 
    # Usually IVaR assumes funded by cash, so total portfolio value is constant 
    # but weights shift. If adding leverage, portfolio_value increases.
    # Here we assume a rebalance (cash -> stock), so value stays same.
    var_new, _ = get_portfolio_var(new_weights, cov_matrix, portfolio_value)
    
    return var_new - var_old

def calculate_individual_vars(cov_matrix, portfolio_value, positions_dollars):
    """
    Calculates Isolated VaR for each stock (as if it were the only holding).
    Formula: Vol_i * Value_i * Z
    """
    # Extract diagonal (Variances) -> Volatilities
    volatilities = np.sqrt(np.diagonal(cov_matrix))
    
    individual_vars = volatilities * positions_dollars * CONFIDENCE_LEVEL
    return individual_vars

def calculate_liquidity_var(quantity, price, bid, ask, avg_daily_volume):
    """
    Estimates the Cost of Liquidation (Liquidity VaR).
    L-VaR = (Half-Spread Cost) + (Market Impact Cost)
    """
    trade_value = quantity * price
    
    # 1. Spread Cost (Immediate loss crossing the spread)
    spread = ask - bid
    half_spread = spread / 2
    cost_spread = quantity * half_spread
    
    # 2. Market Impact (Slippage)
    # Square Root Law: Impact ~ K * Volatility * sqrt(Size / Volume)
    # We use a simplified K constant here.
    if avg_daily_volume > 0:
        participation_rate = quantity / avg_daily_volume
        cost_impact = trade_value * MARKET_IMPACT_K * np.sqrt(participation_rate)
    else:
        cost_impact = trade_value * 0.05 # Fallback penalty if no volume data
        
    return cost_spread + cost_impact

def check_stress_limits(current_weights, stressed_cov_matrix, portfolio_value, limit):
    """
    Comparing Current Portfolio against the 'Crash Matrix' (Mar 2020).
    """
    stressed_var, _ = get_portfolio_var(current_weights, stressed_cov_matrix, portfolio_value)
    
    return {
        "stressed_var": stressed_var,
        "breach": stressed_var > limit,
        "limit": limit
    }
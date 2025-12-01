# 🛠️ Real-Time Risk Engine: Developer Reference Manual

**Version:** 1.0.0
**Target Audience:** Backend Developers, Quant Developers, DevOps
**System Type:** Event-Driven Micro-Service Architecture

---

## 1. System Architecture Overview

The system operates as a distributed state machine. The **Data Engine** pushes state updates to Redis, while the **Dashboard** polls Redis for the latest state. The **Logic Core** acts as the middleware, enforcing business rules and ensuring atomic transactions.

### Tech Stack
* **Language:** Python 3.8+
* **State Store:** Redis (In-Memory Key-Value Store)
* **Math Backend:** NumPy (Matrix Operations)
* **Frontend:** Streamlit
* **Serialization:** Python `pickle` (for complex objects)

---

## 2. Database Schema (Redis)

The system relies on **Redis (db=0)**. Data is serialized using `pickle` to store Python objects (NumPy arrays, Dictionaries).

### Key-Value Reference

| Redis Key | Data Type | deserialized Type | Description |
| :--- | :--- | :--- | :--- |
| **`risk:cov_matrix:current`** | `bytes` | `numpy.ndarray` | Shape $(N \times N)$. The live Variance-Covariance matrix updated via EWMA. |
| **`risk:cov_matrix:stressed`**| `bytes` | `numpy.ndarray` | Shape $(N \times N)$. Static "Crisis" matrix (e.g., March 2020) for stress testing. |
| **`market_data:last_prices`** | `bytes` | `dict` | Map `{ 'TICKER': float_price }`. Used for MTM and Return calculation. |
| **`portfolio:cash`** | `string` | `float` | The current available cash balance (USD). |
| **`portfolio:holdings`** | `bytes` | `dict` | Nested dictionary containing position details. (Structure below). |
| **`config:tickers`** | `bytes` | `list` | List of strings defining the active stock universe. |

### Complex Data Structures

**`portfolio:holdings` Schema:**
```python
{
    "AAPL": {
        "qty": int,          # Total shares held (can be 0)
        "avg_price": float   # Weighted average buy price
    },
    "GOOG": {
        "qty": int,
        "avg_price": float
    },
    ...
}
```
# 💻 Codebase Reference & API

This section details the internal logic, function signatures, and responsibilities of each core module.

## A. The Calculation Kernel (`engine/math_logic.py`)
**Role:** Pure, stateless mathematical functions. **No Redis connections. No Side Effects.**

### `get_portfolio_var(weights, cov_matrix, portfolio_value)`
Calculates the standard Value at Risk.
* **Input:** `weights` ($N \times 1$), `cov_matrix` ($N \times N$), `value` (float).
* **Output:** `(var_dollar, port_std_dev)`.
* **Math:** $\sqrt{w^T \Sigma w} \times 1.65 \times Value$.

### `calculate_marginal_var(weights, cov_matrix, ...)`
Calculates the sensitivity of risk to weight changes.
* **Output:** `numpy.ndarray` ($N \times 1$) representing Dollar Risk Contribution.
* **Math:** $\frac{\Sigma w}{\sigma_{port}}$.

### `calculate_incremental_var(current_weights, trade_delta, ...)`
Simulates "Pre-Trade" vs "Post-Trade" states.
* **Logic:** Calculates $VaR_{new} - VaR_{old}$.
* **Performance Note:** This is the most expensive function ($O(N^2)$) called during user interaction.

### `calculate_liquidity_var(quantity, price, bid, ask, avg_vol)`
Estimates transaction costs.
* **Logic:** Execution Cost (Half-Spread) + Market Impact (Square Root Law).

---

## B. The Logic Controller (`logic/risk_manager.py`)
**Role:** Stateful middleware. Handles Redis I/O and Business Logic.

### Class `RiskManager`

* **`__init__`**: Establishes Redis connection.
* **`get_market_data()`**: Fetches Matrix and Prices. Returns `(None, None)` on failure (fail-safe).
* **`get_portfolio_state()`**: Fetches Cash and Holdings.
* **`get_dashboard_metrics()`**:
    * **Crucial Logic:** This function performs the **Time Scaling**.
    * The engine computes 1-minute volatility. This function multiplies it by $\sqrt{390}$ to display **Daily Volatility** on the dashboard.
    * Constructs the Pandas DataFrame used by the UI.

* **`check_trade_impact(ticker, qty, side)`**:
    * **Role:** Pre-trade Validator.
    * **Checks:**
        1.  Solvency (`Cost <= Cash`).
        2.  Inventory (`Sell Qty <= Held Qty`).
        3.  Risk Limit (`New VaR <= Limit`).
    * **Returns:** Dict containing status (`APPROVED`/`REJECTED`) and risk deltas.

* **`execute_trade(ticker, qty, side)`**:
    * **Role:** State Mutator.
    * **Logic:** Recalculates Weighted Average Price on BUYs. Updates Cash. Serializes and overwrites Redis keys.

---

## C. The Stream Engine (`engine/stream.py`)
**Role:** Background daemon. Simulates market ticks.

* **Global `LAMBDA_DECAY` (0.94):** The forgetting factor for EWMA.
* **`update_covariance_ewma(old, returns, decay)`**:
    * Calculates Outer Product shock: $r \cdot r^T$.
    * Blends with history: $\lambda \Sigma + (1-\lambda) \text{Shock}$.
* **`MockDataStream`**: Generates random walk prices. Replace this class to connect to a real WebSocket (e.g., Alpaca, Polygon).

# 🔄 Data Flow & Lifecycles

Understanding the event loops is critical for debugging latency or state synchronization issues.

## 1. The "Tick" Lifecycle (Background Process)
This process runs infinitely in `engine/stream.py`.

1.  **Ingest:** `stream.py` generates or receives new prices ($P_t$).
2.  **Compute:** Log Returns $r_t = \ln(P_t / P_{t-1})$.
3.  **Update:** Calls `math_logic` to update the Covariance Matrix using EWMA.
4.  **Persist:** `stream.py` writes new Matrix and Prices to Redis keys:
    * `risk:cov_matrix:current`
    * `market_data:last_prices`
5.  **Loop:** Sleeps 2 seconds (simulating 1-minute bar close).

## 2. The "Trade" Lifecycle (User Action)
This process is triggered by the UI in `dashboard/app.py`.

1.  **Input:** User submits trade parameters (Ticker, Side, Qty).
2.  **Query:** `app.py` calls `rm.check_trade_impact()`.
3.  **Validation:** `RiskManager` performs atomic read:
    * Fetches latest Matrix from Redis.
    * Simulates trade impact.
    * Checks against `VAR_LIMIT_DOLLARS`.
4.  **Feedback:** UI displays Green (Go) or Red (No-Go).
5.  **Commit:** User clicks Execute -> `rm.execute_trade()`.
    * Calculates new Cash balance.
    * Updates Holdings.
    * Serializes and overwrites `portfolio:holdings` and `portfolio:cash`.
6.  **Refresh:** `stream.py` (in background) picks up new Prices next tick; `app.py` auto-refreshes visualization.

# 🛠️ Maintenance & Extension Guide

Procedures for modifying the core configuration of the risk engine.

## How to Add a New Stock
The matrix dimensions are fixed at initialization. To add a stock:

1.  **Update Constants:** Add the ticker string to the `TICKERS` list in all 4 files:
    * `engine/warmup.py`
    * `engine/stream.py`
    * `logic/risk_manager.py`
    * `dashboard/app.py`
2.  **Reset Database:** You **must** run `python engine/warmup.py`.
    * *Reason:* The NumPy matrix shape must change (e.g., from $5 \times 5$ to $6 \times 6$). If you don't reset, matrix multiplication will fail due to shape mismatch.

## How to Change Risk Limits
Limits are hardcoded in the Logic layer to prevent UI tampering.

1.  Open `logic/risk_manager.py`.
2.  Modify the constant:
    ```python
    VAR_LIMIT_DOLLARS = 1_000_000 * 0.10  # Change to 10%
    ```

## How to Switch to Live Data
1.  Open `engine/stream.py`.
2.  Replace the `MockDataStream` class with a real WebSocket client (e.g., `alpaca-trade-api` or `ccxt`).
3.  Ensure the client buffers ticks and emits **1-minute bars** (OHLC) to align with the math model.
4.  Feed the close price into the `update_covariance_ewma` function.

# 🛡️ Error Handling & Guardrails

The system uses defensive programming to handle data outages and logical errors without crashing the main dashboard.

| Error Scenario | Detection Mechanism | System Behavior |
| :--- | :--- | :--- |
| **Redis Down** | `RiskManager` catches `redis.exceptions.ConnectionError`. | Dashboard shows empty data or loading spinners. It does **not** crash. Logs error to console. |
| **Empty Portfolio** | `equity_value == 0` check in `risk_manager.py`. | VaR calculations return `0.0`. Volatility calculations handle divide-by-zero gracefully using NumPy safe division. |
| **Corrupt Data** | `pickle.loads` fails or keys return `None`. | `get_state` returns `None`. The UI prompts the user to run the `warmup.py` script to re-seed the DB. |
| **Insolvency** | `check_trade_impact` logic. | Prevents execution if `Trade Value > Available Cash`. |
| **Overselling** | `check_trade_impact` logic. | Prevents execution if `Sell Qty > Current Holdings`. |

# 🧮 Math Glossary

Definitions of the quantitative metrics used throughout the codebase.

### EWMA (Exponentially Weighted Moving Average)
A recursive volatility model that assigns higher weights to recent observations.
* **Formula:** $\sigma_t^2 = \lambda \sigma_{t-1}^2 + (1-\lambda) r_t^2$
* **Why we use it:** It is computationally cheap ($O(1)$) and reacts faster to market shocks than Simple Moving Averages.

### VaR (Value at Risk)
The maximum expected loss over a specific time horizon with a given confidence interval.
* **Confidence:** 95% (1.65 standard deviations).
* **Horizon:** Instantaneous (projected to Daily).

### Marginal VaR (MVaR)
The rate of change of the Portfolio VaR with respect to a change in the weight of a specific asset. It acts as the "Beta" of the position relative to the portfolio's risk.
* **Interpretation:** If MVaR is high, adding more of this stock increases portfolio risk significantly.

### Component VaR
The total dollar amount of risk contributed by a specific asset.
* **Property:** The sum of all Component VaRs equals the Total Portfolio VaR.
* **Formula:** $\text{Marginal VaR}_i \times \text{Weight}_i \times \text{Portfolio Value}$.

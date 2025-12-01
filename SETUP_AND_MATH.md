# 📚 Technical Documentation & Setup Guide

# 📂 Project File Structure

Ensure your directory matches this structure exactly for the imports to work correctly.

```text
ai_risk_trader/
│
├── README.md                 # Main Documentation
├── docs/                     # (Optional) Folder for these documentation files
│
├── engine/                   # DATA LAYER
│   ├── warmup.py             # Seeds Redis with History + Cash
│   ├── stream.py             # Live Market Simulator (EWMA)
│   └── math_logic.py         # Pure Math Formulas (Stateless)
│
├── logic/                    # LOGIC LAYER
│   └── risk_manager.py       # State Manager & Trade Validator
│
└── dashboard/                # UI LAYER
    └── app.py                # Streamlit Frontend
```
# 🧮 Mathematical Methodology

This project uses the **RiskMetrics™** framework adapted for high-frequency simulation.

### A. Volatility Model (EWMA)
We use the RiskMetrics approach to update the Covariance Matrix ($\Sigma$) recursively. This allows the system to react instantly to volatility spikes.

$$\Sigma_t = \lambda \Sigma_{t-1} + (1-\lambda) r_t r_t^T$$

* **Decay Factor ($\lambda$):** 0.94
* **Update Frequency:** Every 2 seconds (Simulated 1-minute bars).

### B. Value at Risk (VaR)
We calculate **95% Confidence VaR**.

$$VaR = 1.65 \times \sigma_{portfolio} \times Value_{portfolio}$$

### C. Time Scaling
Since data flows in 1-minute intervals, raw volatility is tiny. We scale it to **Daily Volatility** for the dashboard using the square root of time rule:

$$\sigma_{daily} = \sigma_{1min} \times \sqrt{390}$$

*(Assuming 6.5 trading hours $\times$ 60 minutes = 390 minutes)*.

### D. Liquidity Risk (L-VaR)
Calculated Pre-Trade to estimate the cost of liquidation (Slippage + Spread):

$$LVaR = (Qty \times \frac{Spread}{2}) + (Value \times k \times \sqrt{\frac{Qty}{AvgVolume}})$$

# ⚙️ Installation & Setup

### Prerequisites
* **Python 3.8+**
* **Redis Server** (Must be running locally on port 6379)

### Step A: Install Dependencies
Create a virtual environment (optional) and install the required packages:

```bash
pip install numpy pandas redis streamlit yfinance
```
# 🏃 Execution Guide (Strict Order)

You must run these scripts in this specific order to initialize the system state correctly.

### 1. Initialize System (Warmup)
Downloads historical data and seeds the account with **$1,000,000**.

```bash
python engine/warmup.py
```

# 💻 Usage Manual

### The Dashboard Layout

1.  **Top Row (Live Prices):** Real-time price feeds for `AAPL`, `GOOG`, `MSFT`, `AMZN`, `TSLA`.
2.  **KPI Header:**
    * **Net Liquidation Value:** Cash + Current Stock Equity.
    * **Available Cash:** Current buying power.
    * **Portfolio VaR:** Current Risk vs Limit ($50,000).
    * **Portfolio Vol (Daily):** The annualized risk of the portfolio.
3.  **Risk Table:** Breakdown of every holding.
    * *Marginal VaR:* Sensitivity (Beta).
    * *Risk Contrib ($):* How much this stock adds to the total risk.
4.  **Heatmap:** Real-time Correlation Matrix (Red = High Correlation).

### How to Trade

1.  Go to the **Sidebar (Execution Blotter)**.
2.  Select Ticker, Side (BUY/SELL), and Quantity.
3.  Click **"Check Risk"**.
    * 🟢 **If Safe:** You see a Green Confirmation with "Incremental VaR". Click **EXECUTE**.
    * 🔴 **If Unsafe:** You see a Red Block "VaR Limit Breached" or "Insufficient Funds".

# 🔧 Troubleshooting Guide

| Error Message | Likely Cause | Solution |
| :--- | :--- | :--- |
| `KeyError: 'AAPL'` | Redis database structure is empty or corrupt. | Run `python engine/warmup.py` again to reset the database. |
| `ConnectionError: Error 61...` | Redis server is not running. | Start `redis-server` in a separate terminal window. |
| `nan` or `0.00` in metrics | Portfolio is strictly empty (no equity). | Buy at least 1 share of any stock to initialize volatility math. |
| `ImportError: No module named logic` | Running script from wrong directory. | Ensure you run all commands from the root `ai_risk_trader/` folder. |

---
**Disclaimer:** This project is for educational purposes only. The market data is simulated (random walk based on history) and should not be used for actual financial trading.

# 🛡️ Real-Time Institutional Risk Engine

A high-frequency, pre-trade risk management system designed to simulate an institutional trading environment. This engine calculates risk metrics in real-time using **EWMA (Exponentially Weighted Moving Average)** models, strictly enforcing VaR limits before allowing trade execution.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Redis](https://img.shields.io/badge/Redis-In--Memory-red.svg)
![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-FF4B4B.svg)

---

## 📖 Project Overview

Unlike standard portfolio trackers, this system separates **Market Risk** (Price movement) from **Liquidity Risk** (Cost of execution). It acts as a gatekeeper: every trade proposed by the user is mathematically analyzed against the current portfolio covariance matrix.

**Key Capabilities:**
* **⚡ Real-Time Volatility:** Updates Variance-Covariance matrix every 2 seconds based on live (simulated) ticks.
* **🛡️ Pre-Trade Guardrails:** Deterministic logic blocks trades if `Projected VaR > Limit` ($50k) or `Cost > Cash`.
* **📊 Risk Decomposition:** Breaks down risk into **Isolated VaR**, **Marginal VaR**, and **Dollar Risk Contribution**.
* **💧 Liquidity Modeling:** Estimates slippage and bid-ask spread costs dynamically based on order size.
* **💾 State Management:** Uses Redis as a high-speed in-memory database to persist cash, holdings, and risk matrices.

---

## 🏗️ System Architecture

The system follows a **Micro-Service Architecture** consisting of three distinct layers:

```mermaid
graph TD
    A[Market Data Sim] -->|WebSocket Ticks| B(Stream Engine)
    B -->|EWMA Update| C[(Redis Database)]
    D[Warmup Script] -->|Seed History| C
    E[Risk Logic Core] <-->|Fetch State| C
    F[Streamlit Dashboard] <-->|Read Metrics| E
    G[User] -->|Execute Trade| F

# Paper Trading Agent

Railway-ready Python service for Alpaca paper trading.

## Safety
This project is paper-only and uses Alpaca's paper=True client mode.
Order submission is disabled by default.
The optional test endpoint is limited to a configurable small notional amount.

## Railway variables
Set:
- ALPACA_API_KEY: your Alpaca paper API key
- ALPACA_API_SECRET: your Alpaca paper API secret
- ENABLE_TEST_ORDERS: false
- MAX_TEST_NOTIONAL: 5

Never commit API credentials to GitHub.

## Endpoints
- GET /health
- GET /account
- GET /positions
- GET /orders
- POST /paper-test-order

## Run
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080

Next we will add a strategy/signal engine and a separate risk engine.

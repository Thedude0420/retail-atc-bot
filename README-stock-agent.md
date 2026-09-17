# Stock Trading Agent

Initial version is **paper-trading only**.

## Architecture
- FastAPI API
- Alpaca paper brokerage API
- Railway deployment
- GitHub source control

## Endpoints
- GET /health
- GET /account
- GET /positions
- POST /orders

Live trading is intentionally not enabled. Before any live execution, add authentication, risk limits, audit logging, explicit order confirmation, and a separate live-broker configuration.

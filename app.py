import os
from datetime import datetime, timezone
from fastapi import FastAPI
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

app = FastAPI(title="Paper Trading Agent", version="0.1.0")

def trading_client() -> TradingClient:
    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_API_SECRET"]
    return TradingClient(key, secret, paper=True)

@app.get("/")
def root():
    return {"service": "paper-trading-agent", "paper_only": True, "status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/account")
def account():
    account = trading_client().get_account()
    return {
        "id": account.id,
        "status": account.status,
        "cash": str(account.cash),
        "buying_power": str(account.buying_power),
        "portfolio_value": str(account.portfolio_value),
        "pattern_day_trader": account.pattern_day_trader,
    }

@app.get("/positions")
def positions():
    positions = trading_client().get_all_positions()
    return [{"symbol": p.symbol, "qty": str(p.qty), "market_value": str(p.market_value),
             "avg_entry_price": str(p.avg_entry_price), "unrealized_pl": str(p.unrealized_pl)}
            for p in positions]

@app.get("/orders")
def orders():
    orders = trading_client().get_orders()
    return [{"id": str(o.id), "symbol": o.symbol, "side": str(o.side), "type": str(o.type),
             "qty": str(o.qty), "status": str(o.status),
             "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None}
            for o in orders]

@app.post("/paper-test-order")
def paper_test_order(symbol: str = "SPY", notional: float = 1.00):
    if os.getenv("ENABLE_TEST_ORDERS", "false").lower() != "true":
        return {"submitted": False, "reason": "Test orders are disabled.", "paper_only": True}
    if notional <= 0 or notional > float(os.getenv("MAX_TEST_NOTIONAL", "5")):
        return {"submitted": False, "reason": "Notional exceeds configured test limit.", "paper_only": True}
    order = trading_client().submit_order(
        order_data=MarketOrderRequest(
            symbol=symbol.upper(), notional=notional,
            side=OrderSide.BUY, time_in_force=TimeInForce.DAY
        )
    )
    return {"submitted": True, "paper_only": True, "order_id": str(order.id),
            "symbol": order.symbol, "status": str(order.status)}

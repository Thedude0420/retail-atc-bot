import os
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from risk import check_risk
from strategy import generate_signal

app = FastAPI(title="Paper Trading Agent", version="0.2.0")


def credentials() -> tuple[str, str]:
    return os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]


def trading_client() -> TradingClient:
    key, secret = credentials()
    return TradingClient(key, secret, paper=True)


def data_client() -> StockHistoricalDataClient:
    key, secret = credentials()
    return StockHistoricalDataClient(key, secret)


@app.get("/")
def root():
    return {
        "service": "paper-trading-agent",
        "paper_only": True,
        "live_trading_enabled": False,
        "orders_enabled": os.getenv("ENABLE_TEST_ORDERS", "false").lower() == "true",
        "status": "ok",
    }


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/account")
def account():
    account = trading_client().get_account()
    return {
        "id": account.id,
        "status": str(account.status),
        "cash": str(account.cash),
        "buying_power": str(account.buying_power),
        "portfolio_value": str(account.portfolio_value),
        "pattern_day_trader": account.pattern_day_trader,
    }


@app.get("/positions")
def positions():
    items = trading_client().get_all_positions()
    return [
        {
            "symbol": p.symbol,
            "qty": str(p.qty),
            "market_value": str(p.market_value),
            "avg_entry_price": str(p.avg_entry_price),
            "unrealized_pl": str(p.unrealized_pl),
        }
        for p in items
    ]


@app.get("/orders")
def orders():
    items = trading_client().get_orders()
    return [
        {
            "id": str(o.id),
            "symbol": o.symbol,
            "side": str(o.side),
            "type": str(o.type),
            "qty": str(o.qty),
            "status": str(o.status),
            "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        }
        for o in items
    ]


def recent_closes(symbol: str, days: int = 40) -> list[float]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    request = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        limit=days,
    )
    bars = data_client().get_stock_bars(request)
    rows = bars.data.get(symbol.upper(), [])
    return [float(bar.close) for bar in rows]


@app.get("/signal")
def signal(symbol: str = "SPY", fast_window: int = 5, slow_window: int = 20):
    try:
        closes = recent_closes(symbol)
        result = generate_signal(symbol, closes, fast_window, slow_window)
        return {
            "symbol": result.symbol,
            "action": result.action,
            "price": result.price,
            "fast_sma": result.fast_sma,
            "slow_sma": result.slow_sma,
            "reason": result.reason,
            "paper_only": True,
            "order_submitted": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/risk-check")
def risk_check(symbol: str = "SPY", proposed_notional: float = 5.0):
    try:
        client = trading_client()
        account = client.get_account()
        positions = client.get_all_positions()
        position_value = 0.0
        for position in positions:
            if position.symbol.upper() == symbol.upper():
                position_value = float(position.market_value)
                break

        decision = check_risk(
            account_equity=float(account.portfolio_value),
            proposed_notional=proposed_notional,
            position_market_value=position_value,
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.10")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02")),
            daily_pnl=0.0,
        )
        return {
            "symbol": symbol.upper(),
            "allowed": decision.allowed,
            "reason": decision.reason,
            "max_notional": decision.max_notional,
            "paper_only": True,
            "order_submitted": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/paper-test-order")
def paper_test_order(symbol: str = "SPY", notional: float = 1.00):
    if os.getenv("ENABLE_TEST_ORDERS", "false").lower() != "true":
        return {"submitted": False, "reason": "Test orders are disabled.", "paper_only": True}
    if notional <= 0 or notional > float(os.getenv("MAX_TEST_NOTIONAL", "5")):
        return {"submitted": False, "reason": "Notional exceeds configured test limit.", "paper_only": True}
    order = trading_client().submit_order(
        order_data=MarketOrderRequest(
            symbol=symbol.upper(),
            notional=notional,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
    )
    return {
        "submitted": True,
        "paper_only": True,
        "order_id": str(order.id),
        "symbol": order.symbol,
        "status": str(order.status),
    }

import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from risk import check_risk
from strategy import generate_signal

app = FastAPI(title="Stock Trading Agent", version="0.4.0")


def live_trading_enabled() -> bool:
    return os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"


def live_order_execution_enabled() -> bool:
    return os.getenv("LIVE_ORDER_EXECUTION_ENABLED", "false").lower() == "true"


def paper_only() -> bool:
    return not live_trading_enabled()


def credentials() -> tuple[str, str]:
    if live_trading_enabled():
        key = os.environ["LIVE_ALPACA_API_KEY"]
        secret = os.environ["LIVE_ALPACA_API_SECRET"]
        return key, secret
    return os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]


def trading_client() -> TradingClient:
    key, secret = credentials()
    return TradingClient(key, secret, paper=paper_only())


def data_client() -> StockHistoricalDataClient:
    key, secret = credentials()
    return StockHistoricalDataClient(key, secret)


def safe_notional_limit() -> float:
    if live_trading_enabled():
        return float(os.getenv("LIVE_ORDER_MAX_NOTIONAL", "5.00"))
    return float(os.getenv("MAX_TEST_NOTIONAL", "5.00"))


@app.get("/")
def root():
    return {
        "service": "stock-trading-agent",
        "paper_only": paper_only(),
        "live_trading_enabled": live_trading_enabled(),
        "live_order_execution_enabled": live_order_execution_enabled(),
        "orders_enabled": os.getenv("ENABLE_TEST_ORDERS", "false").lower() == "true",
        "auto_trading_enabled": os.getenv("AUTO_TRADING_ENABLED", "false").lower() == "true",
        "live_order_max_notional": safe_notional_limit(),
        "status": "ok",
    }


def run_one_paper_test_if_enabled() -> None:
    """Run one idempotent $5 SPY paper BUY for deployment verification."""
    if live_trading_enabled():
        return
    if os.getenv("RUN_ONE_PAPER_TEST", "false").lower() != "true":
        return
    if os.getenv("ENABLE_TEST_ORDERS", "false").lower() != "true":
        print("ONE_PAPER_TEST skipped: ENABLE_TEST_ORDERS is false", flush=True)
        return

    client = trading_client()
    client_order_id = f"retail-atc-paper-test-spy-5-{uuid4().hex[:12]}"

    order = client.submit_order(
        order_data=MarketOrderRequest(
            symbol="SPY",
            notional=5.00,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
    )

    verified = client.get_order_by_client_id(client_order_id)
    print(
        f"ONE_PAPER_TEST submitted order_id={order.id} status={order.status} "
        f"verified_id={verified.id} verified_status={verified.status}",
        flush=True,
    )


@app.on_event("startup")
def startup_test_order() -> None:
    try:
        run_one_paper_test_if_enabled()
    except Exception as exc:
        print(f"ONE_PAPER_TEST failed: {exc}", flush=True)


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
        "last_equity": str(getattr(account, "last_equity", account.portfolio_value)),
        "pattern_day_trader": account.pattern_day_trader,
        "paper_only": paper_only(),
        "live_trading_enabled": live_trading_enabled(),
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
        feed=DataFeed.IEX,
    )
    bars = data_client().get_stock_bars(request)
    rows = bars.data.get(symbol.upper(), [])
    return [float(bar.close) for bar in rows]


def get_position(client: TradingClient, symbol: str):
    for position in client.get_all_positions():
        if position.symbol.upper() == symbol.upper():
            return position
    return None


def has_open_order(client: TradingClient, symbol: str) -> bool:
    open_orders = client.get_orders()
    open_statuses = {"new", "accepted", "pending_new", "partially_filled", "held"}
    return any(
        order.symbol.upper() == symbol.upper() and str(order.status).lower() in open_statuses
        for order in open_orders
    )


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
            "paper_only": paper_only(),
            "live_trading_enabled": live_trading_enabled(),
            "order_submitted": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/data-diagnostic")
def data_diagnostic(symbol: str = "SPY"):
    try:
        closes = recent_closes(symbol, days=40)
        result = generate_signal(symbol, closes, 5, 20)
        return {
            "status": "ok",
            "alpaca_data_connection": "ok",
            "data_feed": "IEX",
            "symbol": result.symbol,
            "bars_received": len(closes),
            "latest_close": result.price,
            "fast_sma": result.fast_sma,
            "slow_sma": result.slow_sma,
            "action": result.action,
            "reason": result.reason,
            "paper_only": paper_only(),
            "live_trading_enabled": live_trading_enabled(),
            "order_submitted": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Alpaca data diagnostic failed: {exc}")


@app.get("/risk-check")
def risk_check_endpoint(symbol: str = "SPY", proposed_notional: float = 5.0):
    try:
        client = trading_client()
        account = client.get_account()
        position = get_position(client, symbol)
        position_value = float(position.market_value) if position else 0.0
        last_equity = float(getattr(account, "last_equity", account.portfolio_value))
        daily_pnl = float(account.portfolio_value) - last_equity

        decision = check_risk(
            account_equity=float(account.portfolio_value),
            proposed_notional=proposed_notional,
            position_market_value=position_value,
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.10")),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02")),
            daily_pnl=daily_pnl,
        )
        return {
            "symbol": symbol.upper(),
            "allowed": decision.allowed,
            "reason": decision.reason,
            "max_notional": decision.max_notional,
            "daily_pnl": daily_pnl,
            "paper_only": paper_only(),
            "live_trading_enabled": live_trading_enabled(),
            "order_submitted": False,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/trade-cycle")
def trade_cycle(
    symbol: str = "SPY",
    proposed_notional: float = 5.0,
    execute: bool = False,
):
    try:
        symbol = symbol.upper()
        client = trading_client()
        account = client.get_account()
        position = get_position(client, symbol)
        position_value = float(position.market_value) if position else 0.0
        position_qty = float(position.qty) if position else 0.0
        last_equity = float(getattr(account, "last_equity", account.portfolio_value))
        daily_pnl = float(account.portfolio_value) - last_equity

        closes = recent_closes(symbol, days=40)
        signal_result = generate_signal(symbol, closes, 5, 20)

        max_position_pct = float(os.getenv("MAX_POSITION_PCT", "0.10"))
        max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))

        if signal_result.action == "SELL":
            if position_qty <= 0:
                return {
                    "status": "no_action",
                    "stage": "position_check",
                    "symbol": symbol,
                    "signal": "SELL",
                    "reason": "SELL signal but no long position is held; short selling is disabled.",
                    "paper_only": paper_only(),
                    "live_trading_enabled": live_trading_enabled(),
                    "order_submitted": False,
                }
            proposed_notional = min(proposed_notional, max(position_value, 0.0))

        if live_trading_enabled() and proposed_notional > safe_notional_limit():
            proposed_notional = safe_notional_limit()

        decision = check_risk(
            account_equity=float(account.portfolio_value),
            proposed_notional=proposed_notional,
            position_market_value=position_value,
            max_position_pct=max_position_pct,
            max_daily_loss_pct=max_daily_loss_pct,
            daily_pnl=daily_pnl,
        )

        result = {
            "status": "ready" if decision.allowed else "blocked",
            "symbol": symbol,
            "signal": signal_result.action,
            "signal_reason": signal_result.reason,
            "price": signal_result.price,
            "fast_sma": signal_result.fast_sma,
            "slow_sma": signal_result.slow_sma,
            "daily_pnl": daily_pnl,
            "risk_allowed": decision.allowed,
            "risk_reason": decision.reason,
            "approved_notional": decision.max_notional,
            "paper_only": paper_only(),
            "live_trading_enabled": live_trading_enabled(),
            "order_submitted": False,
        }

        if not decision.allowed:
            return result

        if signal_result.action == "HOLD":
            result["status"] = "no_action"
            result["risk_reason"] = "No trade signal"
            return result

        if has_open_order(client, symbol):
            result["status"] = "blocked"
            result["risk_reason"] = "Open order already exists for symbol"
            return result

        clock = client.get_clock()
        if not clock.is_open:
            result["status"] = "blocked"
            result["risk_reason"] = "Market is closed"
            return result

        if not execute:
            result["status"] = "ready"
            result["risk_reason"] = "Signal and risk checks passed; execution not requested"
            return result

        if os.getenv("AUTO_TRADING_ENABLED", "false").lower() != "true":
            result["status"] = "blocked"
            result["risk_reason"] = "AUTO_TRADING_ENABLED is false"
            return result

        if paper_only():
            if os.getenv("ENABLE_TEST_ORDERS", "false").lower() != "true":
                result["status"] = "blocked"
                result["risk_reason"] = "ENABLE_TEST_ORDERS is false"
                return result
        else:
            if not live_order_execution_enabled():
                result["status"] = "blocked"
                result["risk_reason"] = "LIVE_ORDER_EXECUTION_ENABLED is false"
                return result

        side = OrderSide.BUY if signal_result.action == "BUY" else OrderSide.SELL
        order = client.submit_order(
            order_data=MarketOrderRequest(
                symbol=symbol,
                notional=decision.max_notional,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
        )
        result["status"] = "submitted"
        result["order_submitted"] = True
        result["order_id"] = str(order.id)
        result["order_status"] = str(order.status)
        return result

    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/paper-test-order")
def paper_test_order(symbol: str = "SPY", notional: float = 1.00):
    if live_trading_enabled():
        return {
            "submitted": False,
            "reason": "Paper test orders are disabled while live trading mode is enabled.",
            "paper_only": False,
        }
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

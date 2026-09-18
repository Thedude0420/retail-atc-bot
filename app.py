import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Header
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

from risk import check_risk
from strategy import generate_signal

app = FastAPI(title="Stock Trading Agent", version="0.5.0")


def require_trade_token(authorization: str | None = Header(default=None)):
    expected = os.getenv("TRADE_API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Trading API authentication is not configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def autonomous_trading_enabled() -> bool:
    return live_trading_enabled() and live_order_execution_enabled() and os.getenv("AUTO_TRADING_ENABLED", "false").lower() == "true"


def autonomous_trade_loop() -> None:
    interval = max(int(os.getenv("AUTO_TRADE_INTERVAL_SECONDS", "900")), 300)
    while True:
        try:
            if autonomous_trading_enabled():
                result = trade_cycle(symbol="SPY", proposed_notional=5.0, execute=True)
                print(f"AUTONOMOUS_TRADE_CYCLE result={result}", flush=True)
        except Exception as exc:
            print(f"AUTONOMOUS_TRADE_CYCLE error={_diagnostic_error(exc)} order_submitted=false", flush=True)
        import time
        time.sleep(interval)

def live_trading_enabled() -> bool:
    return os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

def live_order_execution_enabled() -> bool:
    return os.getenv("LIVE_ORDER_EXECUTION_ENABLED", "false").lower() == "true"

def paper_only() -> bool:
    return not live_trading_enabled()

def credentials() -> tuple[str, str]:
    if live_trading_enabled():
        return os.environ["LIVE_ALPACA_API_KEY"], os.environ["LIVE_ALPACA_API_SECRET"]
    return os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]

def trading_client() -> TradingClient:
    key, secret = credentials()
    return TradingClient(key, secret, paper=paper_only())

def data_client() -> StockHistoricalDataClient:
    key, secret = credentials()
    return StockHistoricalDataClient(key, secret)

def safe_notional_limit() -> float:
    return float(os.getenv("LIVE_ORDER_MAX_NOTIONAL", "5.00")) if live_trading_enabled() else float(os.getenv("MAX_TEST_NOTIONAL", "5.00"))

def live_budget_limit() -> float:
    return float(os.getenv("LIVE_TRADING_BUDGET", "50.00"))

def live_budget_start() -> datetime:
    raw = os.getenv("LIVE_TRADING_START_AT", "")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else datetime.now(timezone.utc) - timedelta(days=30)

def live_budget_spent(client: TradingClient) -> float:
    if not live_trading_enabled():
        return 0.0
    request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500, after=live_budget_start(), nested=True)
    orders = client.get_orders(filter=request)
    spent = 0.0
    for order in orders:
        notional = getattr(order, "notional", None)
        if notional is not None:
            spent += abs(float(notional))
        elif getattr(order, "filled_qty", None) and getattr(order, "filled_avg_price", None):
            spent += abs(float(order.filled_qty) * float(order.filled_avg_price))
    return spent

def live_budget_remaining(client: TradingClient) -> float:
    return max(live_budget_limit() - live_budget_spent(client), 0.0)

def _diagnostic_error(exc: Exception) -> str:
    message = str(exc)
    for name in ("ALPACA_API_KEY","ALPACA_API_SECRET","LIVE_ALPACA_API_KEY","LIVE_ALPACA_API_SECRET"):
        secret = os.getenv(name, "")
        if secret:
            message = message.replace(secret, "***")
    return message

def run_alpaca_connectivity_diagnostic() -> None:
    results = []
    paper_key, paper_secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    if paper_key and paper_secret:
        try:
            TradingClient(paper_key, paper_secret, paper=True).get_account()
            results.append("paper=connected")
        except Exception as exc:
            results.append(f"paper=error error={_diagnostic_error(exc)}")
    else:
        results.append("paper=not_configured")
    live_key, live_secret = os.getenv("LIVE_ALPACA_API_KEY"), os.getenv("LIVE_ALPACA_API_SECRET")
    if live_key and live_secret:
        try:
            TradingClient(live_key, live_secret, paper=False).get_account()
            results.append("live=connected")
        except Exception as exc:
            results.append(f"live=error error={_diagnostic_error(exc)}")
    else:
        results.append("live=not_configured")
    print("ALPACA_DIAGNOSTIC " + " ".join(results) + " orders_submitted=false", flush=True)

def run_readonly_trade_check() -> None:
    if not live_trading_enabled():
        return
    try:
        client = trading_client()
        account = client.get_account()
        closes = recent_closes("SPY")
        sig = generate_signal("SPY", closes, 5, 20)
        remaining = live_budget_remaining(client)
        clock = client.get_clock()
        position = get_position(client, "SPY")
        position_value = float(position.market_value) if position else 0.0
        daily_pnl = float(account.portfolio_value) - float(getattr(account, "last_equity", account.portfolio_value))
        proposed = min(50.0, safe_notional_limit(), remaining)
        decision = check_risk(account_equity=float(account.portfolio_value), proposed_notional=proposed, position_market_value=position_value, max_position_pct=float(os.getenv("MAX_POSITION_PCT","0.10")), max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT","0.02")), daily_pnl=daily_pnl)
        print(f"READONLY_TRADE_CHECK symbol=SPY signal={sig.action} price={sig.price} risk_allowed={decision.allowed} approved_notional={decision.max_notional} market_open={clock.is_open} budget_remaining={remaining} order_submitted=false", flush=True)
    except Exception as exc:
        print(f"READONLY_TRADE_CHECK error={_diagnostic_error(exc)} order_submitted=false", flush=True)

@app.on_event("startup")
def startup():
    try: run_alpaca_connectivity_diagnostic()
    except Exception as exc: print(f"ALPACA_DIAGNOSTIC failed: {_diagnostic_error(exc)}", flush=True)
    try: run_readonly_trade_check()
    except Exception as exc: print(f"READONLY_TRADE_CHECK failed: {_diagnostic_error(exc)}", flush=True)

@app.get("/")
def root():
    return {"service":"stock-trading-agent","paper_only":paper_only(),"live_trading_enabled":live_trading_enabled(),"live_order_execution_enabled":live_order_execution_enabled(),"auto_trading_enabled":os.getenv("AUTO_TRADING_ENABLED","false").lower()=="true","live_order_max_notional":safe_notional_limit(),"status":"ok"}

@app.get("/health")
def health():
    return {"status":"ok","timestamp":datetime.now(timezone.utc).isoformat()}

def recent_closes(symbol: str, days: int = 40) -> list[float]:
    end = datetime.now(timezone.utc)
    request = StockBarsRequest(symbol_or_symbols=symbol.upper(), timeframe=TimeFrame.Day, start=end-timedelta(days=days), end=end, limit=days, feed=DataFeed.IEX)
    bars = data_client().get_stock_bars(request)
    return [float(bar.close) for bar in bars.data.get(symbol.upper(), [])]

def get_position(client: TradingClient, symbol: str):
    for position in client.get_all_positions():
        if position.symbol.upper() == symbol.upper(): return position
    return None

def has_open_order(client: TradingClient, symbol: str) -> bool:
    open_statuses = {"new","accepted","pending_new","partially_filled","held"}
    return any(o.symbol.upper()==symbol.upper() and str(o.status).lower() in open_statuses for o in client.get_orders())

@app.get("/account")
def account():
    a=trading_client().get_account()
    return {"id":str(a.id),"status":str(a.status),"cash":str(a.cash),"buying_power":str(a.buying_power),"portfolio_value":str(a.portfolio_value),"last_equity":str(getattr(a,"last_equity",a.portfolio_value)),"paper_only":paper_only(),"live_trading_enabled":live_trading_enabled()}

@app.get("/positions")
def positions():
    return [{"symbol":p.symbol,"qty":str(p.qty),"market_value":str(p.market_value),"avg_entry_price":str(p.avg_entry_price),"unrealized_pl":str(p.unrealized_pl)} for p in trading_client().get_all_positions()]

@app.get("/orders")
def orders():
    return [{"id":str(o.id),"symbol":o.symbol,"side":str(o.side),"type":str(o.type),"qty":str(o.qty),"status":str(o.status)} for o in trading_client().get_orders()]

@app.get("/signal")
def signal(symbol: str="SPY"):
    closes=recent_closes(symbol)
    r=generate_signal(symbol,closes,5,20)
    return {"symbol":r.symbol,"action":r.action,"price":r.price,"fast_sma":r.fast_sma,"slow_sma":r.slow_sma,"reason":r.reason,"live_trading_enabled":live_trading_enabled(),"order_submitted":False}

@app.get("/trade-cycle")
def trade_cycle(symbol: str="SPY", proposed_notional: float=5.0, execute: bool=False):
    try:
        symbol=symbol.upper(); client=trading_client(); account=client.get_account(); position=get_position(client,symbol)
        position_value=float(position.market_value) if position else 0.0
        position_qty=float(position.qty) if position else 0.0
        daily_pnl=float(account.portfolio_value)-float(getattr(account,"last_equity",account.portfolio_value))
        sig=generate_signal(symbol,recent_closes(symbol),5,20)
        if sig.action=="SELL" and position_qty<=0:
            return {"status":"no_action","stage":"position_check","symbol":symbol,"signal":"SELL","reason":"SELL signal but no long position is held; short selling is disabled.","order_submitted":False}
        if live_trading_enabled():
            proposed_notional=min(proposed_notional,safe_notional_limit(),live_budget_remaining(client))
        decision=check_risk(account_equity=float(account.portfolio_value), proposed_notional=proposed_notional, position_market_value=position_value, max_position_pct=float(os.getenv("MAX_POSITION_PCT","0.10")), max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT","0.02")), daily_pnl=daily_pnl)
        result={"status":"ready" if decision.allowed else "blocked","symbol":symbol,"signal":sig.action,"signal_reason":sig.reason,"price":sig.price,"fast_sma":sig.fast_sma,"slow_sma":sig.slow_sma,"daily_pnl":daily_pnl,"risk_allowed":decision.allowed,"risk_reason":decision.reason,"approved_notional":decision.max_notional,"live_trading_enabled":live_trading_enabled(),"order_submitted":False}
        if live_trading_enabled():
            result.update({"live_budget":live_budget_limit(),"live_budget_spent":live_budget_spent(client),"live_budget_remaining":live_budget_remaining(client)})
        if sig.action=="BUY" and position_qty > 0:\n            result.update(status="no_action", risk_reason="BUY signal while a long position is already held")\n            return result\n        if not decision.allowed or sig.action=="HOLD":
            if sig.action=="HOLD": result.update(status="no_action",risk_reason="No trade signal")
            return result
        if has_open_order(client,symbol):
            result.update(status="blocked",risk_reason="Open order already exists for symbol"); return result
        if not client.get_clock().is_open:
            result.update(status="blocked",risk_reason="Market is closed"); return result
        if not execute:
            result["risk_reason"]="Signal and risk checks passed; execution not requested"; return result
        if os.getenv("AUTO_TRADING_ENABLED","false").lower()!="true":
            result.update(status="blocked",risk_reason="AUTO_TRADING_ENABLED is false"); return result
        if paper_only():
            result.update(status="blocked",risk_reason="Paper trading is disabled for this live configuration"); return result
        if not live_order_execution_enabled():
            result.update(status="blocked",risk_reason="LIVE_ORDER_EXECUTION_ENABLED is false"); return result
        side=OrderSide.BUY if sig.action=="BUY" else OrderSide.SELL
        client_order_id=f"retail-atc-live-{symbol.lower()}-{uuid4().hex}"
        order=client.submit_order(order_data=MarketOrderRequest(symbol=symbol,notional=decision.max_notional,side=side,time_in_force=TimeInForce.DAY,client_order_id=client_order_id))
        verified=client.get_order_by_client_id(client_order_id)
        result.update(status="submitted",order_submitted=True,order_id=str(verified.id),order_status=str(verified.status),verified=True)
        return result
    except Exception as exc:
        raise HTTPException(status_code=400,detail=str(exc))

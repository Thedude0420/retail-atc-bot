import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from broker import PaperBroker

app = FastAPI(title="Stock Trading Agent", version="0.1.0")
broker = PaperBroker()

class Order(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    qty: float = Field(gt=0)
    side: str = Field(pattern="^(buy|sell)$")

@app.get("/health")
def health():
    return {"status": "ok", "mode": broker.mode}

@app.get("/account")
async def account():
    return await broker.account()

@app.get("/positions")
async def positions():
    return await broker.positions()

@app.post("/orders")
async def create_order(order: Order):
    # Paper trading only. Live execution is intentionally disabled.
    try:
        return await broker.submit_order(order.symbol.upper(), order.qty, order.side)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

import os
import httpx

class PaperBroker:
    mode = "paper"

    def __init__(self):
        self.key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_API_SECRET")
        self.base = os.getenv("ALPACA_PAPER_URL", "https://paper-api.alpaca.markets")

    def _headers(self):
        if not self.key or not self.secret:
            raise RuntimeError("Missing ALPACA_API_KEY or ALPACA_API_SECRET")
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    async def account(self):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self.base}/v2/account", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def positions(self):
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self.base}/v2/positions", headers=self._headers())
            r.raise_for_status()
            return r.json()

    async def submit_order(self, symbol, qty, side):
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{self.base}/v2/orders", headers=self._headers(), json=payload)
            r.raise_for_status()
            return r.json()

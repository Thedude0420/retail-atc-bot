from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Signal:
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    fast_sma: float
    slow_sma: float
    price: float
    reason: str


def sma(values: list[float], window: int) -> float:
    if len(values) < window:
        raise ValueError(f"Need at least {window} prices")
    return sum(values[-window:]) / window


def generate_signal(symbol: str, closes: list[float], fast_window: int = 5, slow_window: int = 20) -> Signal:
    if fast_window <= 0 or slow_window <= fast_window:
        raise ValueError("Require 0 < fast_window < slow_window")
    if len(closes) < slow_window:
        raise ValueError(f"Need at least {slow_window} closing prices")

    fast = sma(closes, fast_window)
    slow = sma(closes, slow_window)
    price = closes[-1]

    if fast > slow:
        action = "BUY"
        reason = f"{fast_window}-SMA is above {slow_window}-SMA"
    elif fast < slow:
        action = "SELL"
        reason = f"{fast_window}-SMA is below {slow_window}-SMA"
    else:
        action = "HOLD"
        reason = "Moving averages are equal"

    return Signal(symbol=symbol.upper(), action=action, fast_sma=fast, slow_sma=slow, price=price, reason=reason)

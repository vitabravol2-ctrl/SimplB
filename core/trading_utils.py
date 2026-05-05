from decimal import Decimal, ROUND_DOWN


def round_price_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError("tick_size must be > 0")
    price_d = Decimal(str(price))
    tick_d = Decimal(str(tick_size))
    return float((price_d / tick_d).to_integral_value(rounding=ROUND_DOWN) * tick_d)


def round_qty_to_step(qty: float, step_size: float) -> float:
    if step_size <= 0:
        raise ValueError("step_size must be > 0")
    qty_d = Decimal(str(qty))
    step_d = Decimal(str(step_size))
    return float((qty_d / step_d).to_integral_value(rounding=ROUND_DOWN) * step_d)


def validate_min_notional(qty: float, price: float, min_notional: float) -> bool:
    return qty * price >= min_notional

"""Independent rational audit of recorded units; never calls a domain tool."""
from fractions import Fraction


def unit_amounts(order):
    prices = {(line.id, i): line.unit_cents - line.discount_cents
              for line in order.lines for i in range(line.quantity)}
    total = sum(prices.values())
    shares = {key: Fraction(price * order.coupon_cents, total) if total else Fraction(0)
              for key, price in prices.items()}
    floors = {key: value.numerator // value.denominator for key, value in shares.items()}
    remainder = order.coupon_cents - sum(floors.values())
    winners = set(sorted(prices, key=lambda key: (-(shares[key]-floors[key]), key))[:remainder])
    return {key: price - floors[key] - int(key in winners) for key, price in prices.items()}


def amounts_valid(state):
    amounts = {order.id: unit_amounts(order) for order in state.orders}
    return all(r.amount_cents == sum(amounts[r.order_id][r.line_id, u] for u in r.units)
               for r in state.returns)

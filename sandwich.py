"""MEV sandwich attack simulator (pure Python, no dependencies).

A sandwich attack on a constant-product AMM (Uniswap v2 style, 0.3% fee):

1. Attacker frontruns a victim's buy with their own buy, pushing the price up.
2. The victim's buy executes at the worse price (their slippage tolerance
   decides whether the tx still goes through).
3. The attacker backruns, selling the tokens bought in step 1 at the
   inflated price, pocketing the difference minus gas.

The pool keeps exact ``Fraction`` arithmetic so the profit numbers are
reproducible, and the demo prints a full sandwich round trip.
"""

from fractions import Fraction

__all__ = [
    "Pool",
    "get_amount_out",
    "get_amount_in",
    "sandwich",
    "optimal_frontrun",
    "max_victim_buy",
]

FEE_NUM = 997          # 0.3% fee: 997/1000 of the input reaches the pool
FEE_DEN = 1000


class Pool:
    """A constant-product pool holding ``reserve_a`` of token A (e.g. ETH)
    and ``reserve_b`` of token B (e.g. USDC).

    >>> pool = Pool(Fraction(100), Fraction(200_000))
    >>> pool.spot_price()
    Fraction(2000, 1)
    """

    def __init__(self, reserve_a, reserve_b):
        self.reserve_a = Fraction(reserve_a)
        self.reserve_b = Fraction(reserve_b)

    def spot_price(self):
        """Price of one unit of token A in units of token B.

        >>> Pool(Fraction(10), Fraction(30_000)).spot_price()
        Fraction(3000, 1)
        """
        return self.reserve_b / self.reserve_a

    def price_impact(self, amount_b):
        """Relative spot-price move caused by a hypothetical buy of
        ``amount_b`` token B. The pool is not modified.

        >>> pool = Pool(Fraction(100), Fraction(200_000))
        >>> pool.price_impact(Fraction(2000))
        Fraction(200697, 10000000)
        >>> pool.price_impact(Fraction(0))
        Fraction(0, 1)
        >>> pool.reserve_a  # unchanged
        Fraction(100, 1)
        """
        amount_b = Fraction(amount_b)
        if amount_b < 0:
            raise ValueError("amount must be non-negative")
        if amount_b == 0:
            return Fraction(0)
        out = get_amount_out(amount_b, self.reserve_b, self.reserve_a)
        new_price = (self.reserve_b + amount_b) / (self.reserve_a - out)
        return (new_price - self.spot_price()) / self.spot_price()

    def copy(self):
        """An independent pool with the same reserves."""
        return Pool(self.reserve_a, self.reserve_b)

    def buy(self, amount_b):
        """Buy token A with ``amount_b`` of token B; returns token A received.

        >>> pool = Pool(Fraction(100), Fraction(200_000))
        >>> pool.buy(Fraction(2000))
        Fraction(99700, 100997)
        >>> pool.reserve_a
        Fraction(10000000, 100997)
        >>> pool.reserve_b
        Fraction(202000, 1)
        """
        out = get_amount_out(amount_b, self.reserve_b, self.reserve_a)
        self.reserve_b += Fraction(amount_b)
        self.reserve_a -= out
        return out

    def sell(self, amount_a):
        """Sell ``amount_a`` of token A for token B; returns token B received.

        >>> pool = Pool(Fraction(100), Fraction(200_000))
        >>> pool.sell(Fraction(1))
        Fraction(199400000, 100997)
        >>> pool.reserve_a
        Fraction(101, 1)
        >>> pool.reserve_b
        Fraction(20000000000, 100997)
        """
        out = get_amount_out(amount_a, self.reserve_a, self.reserve_b)
        self.reserve_a += Fraction(amount_a)
        self.reserve_b -= out
        return out


def get_amount_out(amount_in, reserve_in, reserve_out):
    """Uniswap v2 ``getAmountOut``: output received for ``amount_in`` of the
    input token, given the two reserves.

    >>> get_amount_out(Fraction(1), Fraction(100), Fraction(200_000))
    Fraction(199400000, 100997)
    >>> get_amount_out(Fraction(0), Fraction(100), Fraction(200_000))
    Fraction(0, 1)
    """
    amount_in = Fraction(amount_in)
    reserve_in = Fraction(reserve_in)
    reserve_out = Fraction(reserve_out)
    if amount_in <= 0:
        return Fraction(0)
    if reserve_in <= 0 or reserve_out <= 0:
        raise ValueError("reserves must be positive")
    amount_in_with_fee = amount_in * FEE_NUM
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * FEE_DEN + amount_in_with_fee
    return numerator / denominator


def get_amount_in(amount_out, reserve_in, reserve_out):
    """Uniswap v2 ``getAmountIn``: input needed to receive ``amount_out``
    of the output token, given the two reserves.

    >>> get_amount_in(Fraction(1), Fraction(100), Fraction(200_000))
    Fraction(199499003, 199399003)
    >>> get_amount_in(Fraction(200_000), Fraction(100), Fraction(200_000))
    Traceback (most recent call last):
    ...
    ValueError: cannot drain the pool
    """
    amount_out = Fraction(amount_out)
    reserve_in = Fraction(reserve_in)
    reserve_out = Fraction(reserve_out)
    if amount_out <= 0:
        return Fraction(0)
    if amount_out >= reserve_out:
        raise ValueError("cannot drain the pool")
    if reserve_in <= 0 or reserve_out <= 0:
        raise ValueError("reserves must be positive")
    numerator = reserve_in * amount_out * FEE_DEN
    denominator = (reserve_out - amount_out) * FEE_NUM
    return numerator / denominator + 1


def sandwich(pool, victim_in, attacker_in, gas_cost=0):
    """Simulate a full sandwich round trip against ``pool``.

    ``victim_in`` is the victim's buy of token A (paid in token B),
    ``attacker_in`` is the attacker's frontrun buy (also paid in token B),
    ``gas_cost`` is the attacker's total gas bill in token B.

    Returns a dict with every leg of the attack. ``attacker_profit`` is
    negative when the attack loses money (frontrun too big, pool too deep,
    or gas too high).

    >>> pool = Pool(Fraction(100), Fraction(200_000))
    >>> r = sandwich(pool, victim_in=Fraction(10_000),
    ...              attacker_in=Fraction(5_000), gas_cost=Fraction(50))
    >>> r["attacker_profit"] > 0
    True
    >>> r["victim_received"] < get_amount_out(
    ...     Fraction(10_000), Fraction(200_000), Fraction(100))
    True
    >>> r["attacker_proceeds"] > r["attacker_bought"] * r["price_before"]
    True
    """
    victim_in = Fraction(victim_in)
    attacker_in = Fraction(attacker_in)
    gas_cost = Fraction(gas_cost)
    if victim_in <= 0 or attacker_in <= 0:
        raise ValueError("trade sizes must be positive")

    sim = pool.copy()
    price_before = sim.spot_price()

    # 1. Frontrun: attacker buys token A with token B.
    attacker_bought = sim.buy(attacker_in)
    # 2. Victim buys at the pushed-up price.
    victim_received = sim.buy(victim_in)
    # 3. Backrun: attacker sells the token A bought in step 1.
    attacker_proceeds = sim.sell(attacker_bought)

    return {
        "price_before": price_before,
        "price_after_frontrun": sim.spot_price(),
        "attacker_bought": attacker_bought,
        "victim_received": victim_received,
        "attacker_proceeds": attacker_proceeds,
        "attacker_profit": attacker_proceeds - attacker_in - gas_cost,
    }


def optimal_frontrun(pool, victim_in, gas_cost=0, steps=200):
    """Find the frontrun size (a multiple of ``victim_in / steps``) that
    maximises the attacker's profit, by brute-force scan.

    Returns ``(best_size, best_profit)``. A zero profit means the sandwich
    is not worth doing at any size.

    >>> pool = Pool(Fraction(100), Fraction(200_000))
    >>> size, profit = optimal_frontrun(pool, Fraction(10_000),
    ...                                 gas_cost=Fraction(50))
    >>> size > 0 and profit > 0
    True
    >>> profit == sandwich(pool, Fraction(10_000), size,
    ...                    gas_cost=Fraction(50))["attacker_profit"]
    True
    """
    victim_in = Fraction(victim_in)
    gas_cost = Fraction(gas_cost)
    best_size = Fraction(0)
    best_profit = Fraction(0)
    for i in range(1, steps + 1):
        size = victim_in * i / steps
        profit = sandwich(pool, victim_in, size, gas_cost)["attacker_profit"]
        if profit > best_profit:
            best_size, best_profit = size, profit
    return best_size, best_profit


def max_victim_buy(pool, max_slippage_bps, attacker_in=0):
    """Largest victim buy (in token B) whose execution price stays within
    ``max_slippage_bps`` basis points of the spot price, after an optional
    frontrun of ``attacker_in``.

    Slippage tolerance is the victim's main defence: a tight cap makes the
    victim's tx revert instead of executing at the sandwiched price.

    >>> pool = Pool(Fraction(100), Fraction(200_000))
    >>> cap = max_victim_buy(pool, 50)  # 0.5% slippage cap, no frontrun
    >>> Fraction(390) < cap < Fraction(410)
    True
    >>> max_victim_buy(pool, 0) == Fraction(0)
    True
    """
    sim = pool.copy()
    if attacker_in:
        sim.buy(Fraction(attacker_in))
    spot = sim.spot_price()
    # Execution price of a buy of size x: x / get_amount_out(x).
    # Solve x / out(x) <= spot * (1 + bps/10000) for x by bisection.
    limit = spot * (10000 + max_slippage_bps) / 10000

    def exec_price(x):
        return x / get_amount_out(x, sim.reserve_b, sim.reserve_a)

    lo, hi = Fraction(0), sim.reserve_b
    while exec_price(hi) <= limit:  # hi must be above the answer
        hi *= 2
    for _ in range(80):
        mid = (lo + hi) / 2
        if exec_price(mid) <= limit:
            lo = mid
        else:
            hi = mid
    return lo


def _demo():
    pool = Pool(Fraction(100), Fraction(200_000))  # 100 ETH / 200k USDC
    victim_in = Fraction(10_000)                    # victim buys 10k USDC of ETH
    size, profit = optimal_frontrun(pool, victim_in, gas_cost=Fraction(50))
    result = sandwich(pool, victim_in, size, gas_cost=Fraction(50))
    print(f"pool: {pool.reserve_a} ETH / {pool.reserve_b} USDC "
          f"(spot {float(pool.spot_price()):,.0f} USDC/ETH)")
    print(f"victim buys {victim_in} USDC of ETH")
    print(f"optimal frontrun: {float(size):,.0f} USDC")
    print(f"  attacker buys  {float(result['attacker_bought']):.4f} ETH")
    print(f"  victim gets    {float(result['victim_received']):.4f} ETH "
          f"(vs {float(get_amount_out(victim_in, 200_000, 100)):.4f} ETH "
          f"without the attack)")
    print(f"  attacker sells for {float(result['attacker_proceeds']):,.2f} USDC")
    print(f"  attacker profit: {float(result['attacker_profit']):,.2f} USDC "
          f"(after 50 USDC gas)")
    cap = max_victim_buy(pool, 50)
    print(f"with a 0.5% slippage cap the victim could only buy "
          f"{float(cap):,.0f} USDC before the tx would revert")


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        import doctest

        failures, _ = doctest.testmod()
        raise SystemExit(1 if failures else 0)
    _demo()

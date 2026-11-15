# mev-sandwich-sim

A pure-Python simulator of an MEV sandwich attack on a constant-product AMM
(Uniswap v2 style, 0.3% fee). No dependencies — exact `Fraction` arithmetic
throughout, so every profit figure is reproducible.

## The attack

1. **Frontrun** — the attacker buys token A with token B ahead of the
   victim, pushing the price up.
2. **Victim buy** — the victim's buy executes at the worse price.
3. **Backrun** — the attacker sells the token A bought in step 1 at the
   inflated price, pocketing the difference minus gas.

## Usage

```bash
python3 sandwich.py            # run the demo
python3 sandwich.py --test     # run the doctests
```

```python
from fractions import Fraction
from sandwich import Pool, sandwich, optimal_frontrun, max_victim_buy

pool = Pool(Fraction(100), Fraction(200_000))  # 100 ETH / 200k USDC

# Full round trip: victim buys 10k USDC of ETH, attacker frontruns 5k USDC
result = sandwich(pool, victim_in=Fraction(10_000),
                  attacker_in=Fraction(5_000), gas_cost=Fraction(50))
result["attacker_profit"]   # Fraction, positive when the attack pays

# Best frontrun size for a given victim trade (brute-force scan)
size, profit = optimal_frontrun(pool, Fraction(10_000), gas_cost=Fraction(50))

# The victim's defence: largest buy that stays within a slippage cap
max_victim_buy(pool, 50)    # 0.5% cap — about 398 USDC here
max_victim_buy(pool, 0)     # 0 — the 0.3% fee alone already exceeds spot
```

## Demo output

```
pool: 100 ETH / 200000 USDC (spot 2,000 USDC/ETH)
victim buys 10000 USDC of ETH
optimal frontrun: 10,000 USDC
  attacker buys  4.7483 ETH
  victim gets    4.3172 ETH (vs 4.7483 ETH without the attack)
  attacker sells for 10,886.46 USDC
  attacker profit: 836.46 USDC (after 50 USDC gas)
with a 0.5% slippage cap the victim could only buy 398 USDC before the tx would revert
```

## Takeaway

A sandwich is only profitable when the victim's trade is large relative to
the pool and the attacker's gas is low. The victim's cheapest defence is a
tight slippage tolerance — with a 0.5% cap on this pool, a 10,000 USDC buy
would simply revert instead of executing at the sandwiched price.
```

## What it covers

- `Pool` — constant-product pool with `buy` / `sell` and `spot_price`
- `get_amount_out` / `get_amount_in` — Uniswap v2 swap math
- `sandwich` — full frontrun → victim buy → backrun round trip
- `optimal_frontrun` — brute-force scan for the most profitable frontrun size
- `max_victim_buy` — slippage-cap defence, via bisection on execution price
```

## License

MIT

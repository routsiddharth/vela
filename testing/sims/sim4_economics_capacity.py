"""
SIM 4 — Economics & capacity (deterministic). Tests Claim E ($10k/day, $1M).

No randomness: just the arithmetic of how much volume the claimed profit requires
and whether the observed market is deep enough. Numbers anchored to the live book
(~30k contracts resting per side per 15-min window; 1c top-of-book spread).
"""
import math

def fee_c(p):  # cents per contract, rounded up
    return math.ceil(0.07 * p * (1 - p) * 100.0)

TARGET = 10_000.0          # $/day
WINDOWS_PER_DAY = 96       # 15-min windows, 24h
RESTING_PER_SIDE = 30_000  # observed
TRADED_FRACTION = 0.12     # traded volume ~ 5-20% of resting; midpoint

print("== Per-archetype net edge (cents/contract) ==")
for name, p, gross in [("latency scalp @0.50", 0.50, 2.0),
                       ("repricing edge @0.70", 0.70, 2.5),
                       ("TWAP near-lock @0.97", 0.97, 2.5),
                       ("spread capture @0.50", 0.50, 1.0)]:
    print(f"  {name:<24} gross {gross:.1f}c - fee {fee_c(p)}c = net {gross-fee_c(p):+.1f}c")

print("\n== Contracts/day required for $10k net ==")
print(f"  {'net c/contract':>14} {'contracts/day':>15} {'per window':>12}")
for net in (2.0, 1.0, 0.5, 0.25):
    n = TARGET*100/net
    print(f"  {net:>13.2f}c {n:>15,.0f} {n/WINDOWS_PER_DAY:>12,.0f}")

print("\n== Market depth reality ==")
traded_per_window = RESTING_PER_SIDE * TRADED_FRACTION
traded_day = traded_per_window * WINDOWS_PER_DAY
print(f"  resting/side/window      : {RESTING_PER_SIDE:,}")
print(f"  est. traded/window (~{TRADED_FRACTION:.0%}) : {traded_per_window:,.0f}")
print(f"  est. traded/day (1 market): {traded_day:,.0f} contracts")
for net in (2.0, 1.0, 0.5):
    need = TARGET*100/net
    print(f"  to net $10k @ {net:.1f}c you must be counterparty to "
          f"{need/traded_day:4.1f}x the market's ENTIRE daily traded volume")

print("\n== Fee drag at scale (mid-price churn) ==")
for net in (1.0, 0.5):
    n = TARGET*100/net
    fees = n * fee_c(0.50)/100.0
    gross = TARGET + fees
    print(f"  net {net:.1f}c: {n:,.0f} contracts -> fees ${fees:,.0f}/day, "
          f"gross ${gross:,.0f}, fee/gross = {fees/gross:.0%}")

print("\n== Realistic single-participant capacity (organic edge) ==")
for share in (0.05, 0.15):
    cap = traded_day * share
    for net in (0.5, 1.0):
        print(f"  capture {share:.0%} of flow ({cap:,.0f} contracts) @ {net:.1f}c net "
              f"=> ${cap*net/100:,.0f}/day")

print("\n== $1M cumulative consistency ==")
for daily in (10_000, 900, 500):
    print(f"  at ${daily:,}/day -> $1M in {1_000_000/daily:,.0f} trading days "
          f"(~{1_000_000/daily/21:.1f} months)")

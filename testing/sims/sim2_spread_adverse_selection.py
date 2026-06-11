"""
SIM 2 — Two-sided market making: spread capture vs fees vs adverse selection.

Tests Claim A ("running both sides is a riskless floor") and the fee economics of
Claim E. The bot rests a YES bid and a NO bid (= a YES offer) around 0.50 with a
half-spread h, so spread = 2h. Per window the outcome O in {0,1} is a coin flip.

Adverse selection: informed flow lifts the leg moving in-the-money, so the bot's
LOSING leg fills more often than its winning leg. We parameterize this with an
asymmetry delta: P(fill | losing leg) = q(1+delta), P(fill | winning leg) = q(1-delta).
delta=0 -> symmetric (textbook spread capture); delta->1 -> pure adverse selection.

Fee per filled contract = ceil_cent(0.07*P*(1-P)). Optional maker rebate r (cents)
per filled contract models a designated-MM incentive.

We report net cents per filled contract across scenarios.
"""
import math, random
random.seed(11)

N = 200_000
q = 0.5          # base per-leg fill prob per window

def fee_cents(p):
    raw = 0.07 * p * (1 - p) * 100.0     # in cents
    return math.ceil(raw)                # Kalshi rounds up to the cent

def run(h, delta, rebate_c=0.0, charge_fee=True):
    """h = half-spread (dollars). Returns net cents per filled contract."""
    yes_bid = 0.50 - h
    no_bid  = 0.50 - h          # symmetric; YES offer = 1 - no_bid = 0.50 + h
    f_yb = fee_cents(yes_bid) if charge_fee else 0
    f_nb = fee_cents(no_bid) if charge_fee else 0
    total_pnl_c = 0.0           # cents
    fills = 0
    for _ in range(N):
        O = 1 if random.random() < 0.5 else 0   # 1 => YES wins (price rose)
        # When YES wins, the bot's NO bid (=YES offer) is the LOSING leg (bot sold
        # YES into a win). The YES bid is the winning leg (bot bought YES cheap).
        # When YES loses, the YES bid is the losing leg, the NO bid is the winner.
        if O == 1:
            p_no  = q * (1 + delta)   # losing leg fills more
            p_yes = q * (1 - delta)
        else:
            p_yes = q * (1 + delta)
            p_no  = q * (1 - delta)
        # YES bid fills: bot buys YES at yes_bid, receives O
        if random.random() < min(1.0, p_yes):
            total_pnl_c += (O * 100.0 - yes_bid * 100.0) - f_yb + rebate_c
            fills += 1
        # NO bid fills: bot buys NO at no_bid, receives (1-O)
        if random.random() < min(1.0, p_no):
            total_pnl_c += ((1 - O) * 100.0 - no_bid * 100.0) - f_nb + rebate_c
            fills += 1
    return total_pnl_c / fills if fills else float('nan'), fills

print("Two-sided MM around 0.50.  fee@0.50 = %dc/contract (rounded up)\n" % fee_cents(0.50))
scenarios = [
    ("1c spread, symmetric fills, NO fee model (textbook)", 0.005, 0.0, None),
    ("1c spread, symmetric fills, with fees",               0.005, 0.0, 0.0),
    ("1c spread, MILD adverse selection (delta=0.2)",       0.005, 0.2, 0.0),
    ("1c spread, STRONG adverse selection (delta=0.5)",     0.005, 0.5, 0.0),
    ("3c spread, mild adverse selection (delta=0.2)",       0.015, 0.2, 0.0),
    ("5c spread, mild adverse selection (delta=0.2)",       0.025, 0.2, 0.0),
    ("1c spread, mild adverse (d=0.2) + 1.75c MAKER REBATE", 0.005, 0.2, 1.75),
    ("1c spread, strong adverse(d=0.5) + 1.75c MAKER REBATE",0.005, 0.5, 1.75),
]
print(f"{'scenario':<52} {'net c/contract':>15}")
print("-"*70)
for name, h, delta, reb in scenarios:
    if reb is None:
        net, _ = run(h, delta, 0.0, charge_fee=False)   # textbook: no fee, no rebate
    else:
        net, _ = run(h, delta, reb, charge_fee=True)
    print(f"{name:<52} {net:>14.2f}c")
print("\nInterpretation: positive => profitable per contract; the 1c spread is the live-observed market.")

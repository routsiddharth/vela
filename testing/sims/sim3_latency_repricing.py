"""
SIM 3 — Latency / staleness repricing edge.

Tests Claims B & C: the only robust source of "edge = fair_prob - market_price" on
a 15-min ATM binary is repricing live spot into fair value FASTER than the resting
Kalshi quote updates -- not forecasting BTC direction.

Model (in probability space, which is what the fair value lives in):
  - true fair_t = a random walk driven by live spot (martingale), reflected in [0,1].
  - the Kalshi book quote = fair LAGGED by L seconds, then discretized to the 1c tick.
  - The bot computes fair_t with ~zero lag. When the lagged, discretized quote is
    stale by more than the fee, the bot takes it. Its expected profit on a taken
    contract is (fair_t - price) [since fair_t is the true win prob], minus fee.
  - Competition: the bot only wins a fraction PHI of opportunities (other latency
    bots race for the same stale quote).

We sweep the quote lag L and report: opportunities/hour, average gross edge, net
edge after fee, and a $/day estimate at a fixed contracts-per-fill.
"""
import math, random
random.seed(23)

SIGMA_ANN = 0.60
SEC_PER_YEAR = 365*24*3600
# Map spot vol into fair-value (probability) vol. For an ATM 15-min binary the fair
# value's per-second volatility is ~ phi(0)*d(spot-dist)/d... we calibrate it so the
# probability random walk has a realistic per-second std. A 0.01% spot move ~5min out
# shifts fair by ~5pp (from sim/derivation), so per-second prob-sigma ~ a few tenths pp.
PROB_SIGMA_SEC = 0.004     # 0.4 percentage points per second (reflecting active windows)
TICK = 0.01
SECONDS = 6 * 3600         # simulate 6 hours of active windows
PHI = 0.35                 # fraction of opportunities the bot actually wins (competition)
CONTRACTS_PER_FILL = 50

def fee_cents(p):
    return math.ceil(0.07 * p * (1 - p) * 100.0)

def reflect(x):
    if x < 0.02: return 0.02 + (0.02 - x)
    if x > 0.98: return 0.98 - (x - 0.98)
    return x

def run(L):
    # build fair path
    fair = [0.5]*(SECONDS+1)
    for t in range(1, SECONDS+1):
        fair[t] = reflect(fair[t-1] + random.gauss(0, PROB_SIGMA_SEC))
    opps = 0
    gross_c = 0.0
    net_c = 0.0
    for t in range(L+1, SECONDS+1):
        quote = round(fair[t-1-L] / TICK) * TICK    # stale + discretized
        true = fair[t]
        edge = true - quote                          # >0 => quote too cheap, BUY
        fee = fee_cents(quote) / 100.0
        if abs(edge) > fee:                          # profitable after fee
            if random.random() < PHI:                # win the race
                opps += 1
                gross_c += abs(edge) * 100.0
                net_c += (abs(edge) - fee) * 100.0
    hours = SECONDS/3600.0
    avg_gross = gross_c/opps if opps else 0
    avg_net = net_c/opps if opps else 0
    usd_day = net_c/100.0 * CONTRACTS_PER_FILL * (24/hours)
    return opps/hours, avg_gross, avg_net, usd_day

print(f"prob_sigma_sec = {PROB_SIGMA_SEC} ({PROB_SIGMA_SEC*100:.2f}pp/s), competition PHI={PHI}, {CONTRACTS_PER_FILL} contracts/fill\n")
print(f"{'quote_lag(s)':>12} {'opps/hr':>9} {'avg_gross':>10} {'avg_net':>9} {'$/day(1 mkt)':>13}")
print("-"*60)
for L in (0, 1, 2, 5, 10, 30):
    o, g, n, usd = run(L)
    print(f"{L:>12} {o:>9.1f} {g:>9.2f}c {n:>8.2f}c {usd:>12,.0f}")
print("\nL=0 is the bot's own latency floor (only discretization to exploit).")
print("Edge GROWS with the quote's staleness L: the bot is harvesting the book's lag,")
print("NOT forecasting BTC. If competitors are equally fast, L->0 and the edge collapses.")

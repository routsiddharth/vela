"""
SIM 1 — TWAP-settlement endgame for KXBTC15M.

Kalshi settles the 15-min BTC up/down market on the AVERAGE of 60 ~1Hz CF
Benchmarks RTI samples over the final 60 seconds (a 1-minute TWAP), struck
at-the-money at window open. This sim tests Claim D: that the averaging makes the
outcome progressively "locked" before expiry, producing genuine 97-99c near-locks,
and that a TWAP-aware estimator beats a naive last-price trader.

Pure stdlib. Model: BTC log-price is a driftless random walk (martingale) at the
sub-15-min horizon (justified in findings 02/03). sigma_annual = 0.60.

We simulate:
  - cumulative move over the first 840s -> price at start of final minute (t=840),
  - then 60 one-second samples (t=841..900); settle = mean of those 60.
Decision points: j = 0 (start of final minute, 60 samples to go) and j = 30
(halfway through the averaging window, 30 locked / 30 to go).

Outputs:
  (1) Fraction of windows already "near-locked" (cond. prob >0.9 or <0.1) at each j.
  (2) Calibration of the analytic TWAP-aware estimator vs realized outcomes.
  (3) Edge vs a NAIVE trader who prices settle as the instantaneous close price
      (ignores that a 60-sample average has lower variance than the endpoint).
"""
import math, random
random.seed(7)

SIGMA_ANN = 0.60
SEC_PER_YEAR = 365 * 24 * 3600
SIGMA_SEC = SIGMA_ANN / math.sqrt(SEC_PER_YEAR)   # ~1.07e-4 per second
S_OPEN = 100_000.0
K = S_OPEN                                          # struck ATM at open
N = 60_000                                          # Monte Carlo paths

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def twap_aware_estimate(A, S_t, m):
    """P(settle >= K) given j locked samples summing to A, current price S_t,
    and m = 60 - j samples remaining. Remaining samples are a random walk from
    S_t; settle = (A + R)/60 where R = sum of the m future samples.
    Var(R) = S_t^2 * sigma_sec^2 * sum_{k=1..m} k^2  (linear-return approx)."""
    mean_R = m * S_t
    sum_k2 = m * (m + 1) * (2 * m + 1) / 6.0
    var_R = (S_t ** 2) * (SIGMA_SEC ** 2) * sum_k2
    sd_R = math.sqrt(var_R)
    threshold_R = 60.0 * K - A                      # settle>=K  <=>  R >= 60K - A
    return 1.0 - norm_cdf((threshold_R - mean_R) / sd_R)

def naive_estimate(S_t, secs_to_close):
    """Naive trader: prices settle as the INSTANTANEOUS close price at t=900,
    i.e. P(S_900 >= K). Ignores TWAP variance reduction."""
    if secs_to_close <= 0:
        return 1.0 if S_t >= K else 0.0
    sd = SIGMA_SEC * math.sqrt(secs_to_close)
    return 1.0 - norm_cdf((math.log(K / S_t)) / sd)

# ---- run paths ----
decisions = {0: [], 30: []}     # j -> list of (twap_est, naive_est, realized)
locked_counts = {0: 0, 30: 0}
near_lock = {0: 0, 30: 0}

for _ in range(N):
    # price at start of final minute (t=840)
    s840 = S_OPEN * math.exp(random.gauss(0.0, SIGMA_SEC * math.sqrt(840)))
    # 60 one-second samples
    samples = []
    s = s840
    for _i in range(60):
        s *= math.exp(random.gauss(0.0, SIGMA_SEC))
        samples.append(s)
    settle = sum(samples) / 60.0
    realized = 1.0 if settle >= K else 0.0

    for j in (0, 30):
        A = sum(samples[:j])
        S_t = s840 if j == 0 else samples[j - 1]
        m = 60 - j
        secs_to_close = 60 - j
        te = twap_aware_estimate(A, S_t, m)
        ne = naive_estimate(S_t, secs_to_close)
        decisions[j].append((te, ne, realized))
        if te > 0.9 or te < 0.1:
            near_lock[j] += 1

def calibration(rows, est_idx):
    bins = [(0,0.1),(0.1,0.3),(0.3,0.5),(0.5,0.7),(0.7,0.9),(0.9,1.01)]
    out = []
    for lo,hi in bins:
        sel = [r for r in rows if lo <= r[est_idx] < hi]
        if not sel:
            out.append((lo,hi,0,None,None)); continue
        mean_est = sum(r[est_idx] for r in sel)/len(sel)
        realized = sum(r[2] for r in sel)/len(sel)
        out.append((lo,hi,len(sel),mean_est,realized))
    return out

print(f"sigma_sec = {SIGMA_SEC:.3e} per second   (sigma_annual={SIGMA_ANN})")
print(f"paths = {N},  strike = open = {S_OPEN:.0f}\n")

for j in (0, 30):
    rows = decisions[j]
    print(f"===== DECISION at j={j} locked samples  ({60-j}s of averaging left) =====")
    print(f"  near-lock fraction (TWAP-aware est >0.9 or <0.1): {near_lock[j]/N:6.1%}")
    print(f"  CALIBRATION of TWAP-aware estimator:")
    print(f"    bin            n      mean_est   realized")
    for lo,hi,n,me,re in calibration(rows,0):
        if n: print(f"    [{lo:.2f},{hi:.2f})  {n:6d}    {me:6.3f}     {re:6.3f}")
    # edge vs naive: signed bias and RMS gap
    gaps = [r[0]-r[1] for r in rows]
    rms = math.sqrt(sum(g*g for g in gaps)/len(gaps))
    bias = sum(gaps)/len(gaps)
    big = sum(1 for g in gaps if abs(g) > 0.03)/len(gaps)
    print(f"  TWAP-aware vs NAIVE last-price estimate:")
    print(f"    RMS gap = {rms:6.3f} ({rms*100:.2f}c) | mean signed = {bias:+.3f} | |gap|>3c in {big:5.1%} of windows")
    print()

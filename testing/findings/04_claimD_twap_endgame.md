# Claim D — TWAP-settlement endgame (the likely REAL edge)

**Focus:** settlement = mean of 60 ~1Hz CF Benchmarks RTI samples over the final 60s (a 1-min
TWAP), struck ATM at open. This most plausibly explains the "99¢ near-locks."

## Endgame lock model
After k of 60 samples are in with running mean m_k, `settle = [k·m_k + Σ remaining]/60`. The
outcome (settle ≥ K?) freezes as k grows: the locked block `k·m_k` can't be overturned by the
shrinking `(60−k)`-sample tail (each remaining sample weighted only 1/60).

Per-second sigma from 60% annual vol over ≈3.15e7 s/yr: **σ_sec ≈ 1.07e-4 = 0.0107%/s**.

**Scenario:** strike = S_open; spot now **+0.15%**; 40/60 samples averaged ≈ +0.10% above
strike. To flip below strike the remaining 20-sample mean must be < **−0.20%**
(`(40·0.10 + 20·x̄)/60 < 0`), i.e. ~−0.35% below *current* spot. Std of the 20-sample tail ≈
σ_sec·√20 ≈ 0.048% ⇒ a −0.35% move ≈ **7σ**, `P(flip) ≈ Φ(−7.3) ≈ 1e-13`. So fair ≈ **0.999**
at k=40; even a coarse earlier read (k≈30, +0.08%) lands ~**0.97–0.99**. This is the
"near-lock" regime.

## Why it's a low-variance edge
A bot reconstructing the same RTI samples from its own spot feed computes the live running TWAP
and the conditional settlement distribution directly — it *knows* "97¢" is worth ~99¢ because
it counted the locked samples. Casual traders watch *last-trade price* (instantaneous) and
systematically underestimate how locked a TWAP outcome already is mid-window. The bot
repeatedly buys genuinely-underpriced near-locks; variance is low because conditional p is
known to 4 decimals and ≈ 0.99.

## Manipulation dampening
Averaging 60 samples means a single spike/last tick moves settlement by only ~1/60 (1.7%) of
its size. To drag settlement −0.20% a whale must hold spot displaced across *many* final
seconds — far more capital/risk than sniping one print. Vs instantaneous-close settlement: **no
endgame gamma/snap**, smooth convergence, far less single-print tail risk → the conditional
fair is highly estimable.

## Cost & break-even at 97¢
Fee at P=0.97 = `0.07·0.97·0.03 ≈ 0.20¢` (round-up to 1¢ bites). Break-even at 97¢ entry: win
3¢, risk 97¢ → required win rate `p ≥ 97.2%`. Model p ≈ 99.9% → ~2.7pt margin/trade.
**Asymmetric:** 33 wins of +3¢ wiped by one −97¢ loss. Real only if conditional-p is
well-calibrated; feed lag, stale RTI reconstruction, or a fat-tailed jump turns a "7σ-safe"
lock into a loss. Capacity bounded by thin resting size near 97–99¢.

## Verdict
**Real edge — confidence ~80%.** TWAP mechanics genuinely lock the outcome before expiry; a
feed-synchronized bot prices the conditional distribution far better than last-trade watchers.
**Size: modest** — pennies/contract, ~2–3pt over break-even, capacity-limited by thin tails; a
steady low-variance grind, not large alpha. **Risk: asymmetric blow-up** — calibration error /
jump risk on the rare flip dominates P&L; survival needs accurate σ/jump modeling, low-latency
RTI reconstruction, strict sizing. **Understated jump risk is the main way this is wrong.**

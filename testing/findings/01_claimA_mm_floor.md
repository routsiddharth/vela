# Claim A — Two-sided market-making "floor" (adversarial review)

**Claim:** "The bot runs both sides of the same window at once; this two-sided MM is the riskless floor."

## Mechanism
Rest a YES bid and a NO bid on the same window. Since `yes_ask = 1 − best_no_bid`,
resting NO@0.24 = offering YES@0.76. If both legs fill you hold 1 YES + 1 NO = a
guaranteed $1.00, acquired for 0.99 → 1¢ gross "spread capture," framed as riskless.

## Adversarial attack
Not riskless — the two fills are neither simultaneous nor price-independent.
- **Adverse selection / one-legged fills.** You only collect 1¢ if *both* legs fill near
  mid. Flow is informed: if BTC ticks up, true YES fair → 0.80+, informed takers hit your
  NO bid (buy YES from you at 0.76) and leave your YES bid at 0.75 unfilled. You're now
  short the *winning* side, unhedged. Symmetric on a down-tick. You systematically get the
  leg that's about to lose.
- **Quantify.** If a move pushes true YES fair → 0.85, your NO@0.24 fills (sold YES@0.76),
  MTM loss ≈ 0.85 − 0.76 = **9¢/contract** — wiping out **nine** clean 1¢ captures. One 1%
  BTC move against the unhedged leg dwarfs the spread income from thousands of paired fills.
- **Inventory risk.** Asymmetric fills mean you accumulate directional inventory exactly when
  you least want it. The neutral pair is the *rare* benign case, not the mode.

## Break-even math
Fee/contract = `ceil_cent(0.07·P·(1−P))`, paid **per leg**:
- **P=0.50:** 1.75¢/leg → pair ≈ **3.5¢**. Gross 1¢ ⇒ **−2.5¢ net**.
- **P=0.75:** 1.31¢→2¢ after round-up → pair ≈ **2.6–4¢**. Gross 1¢ ⇒ **−1.6 to −3¢ net**.
Break-even spread ≈ **3.5¢** at P=0.5, **2.6¢** at P=0.75. Observed spread is **1¢** ⇒
**fee-negative by ~2–3¢** under standard maker fees. If makers are exempt/rebated, the lock
becomes +1¢ — *but only if both legs fill*, which adverse selection prevents.

## How a real MM does it
Quotes around a **model fair value** (live spot + τ + TWAP), not the book mid; **cancel/
replaces sub-second** to avoid being picked off; **skews size by inventory** to flatten;
relies on **queue priority** and on **designated-MM rebate/fee-exemption** programs. The edge
is *avoiding one-legged adverse fills* and *earning rebates* — not the 1¢ book spread.

## Verdict
**DOES NOT WORK as literally stated — confidence HIGH.** Fails on two independent grounds:
(1) standard quadratic fees make the 1¢ lock negative by ~2–3¢; (2) even fee-free it isn't
riskless — adverse selection delivers one-legged fills on the losing side. It approaches a
"floor" only under (a) maker fee exemption/rebate **and** (b) symmetric fills near mid — and
(b) is exactly what informed flow denies. **Hinge:** the false assumption that *both legs fill
near mid independent of price*.

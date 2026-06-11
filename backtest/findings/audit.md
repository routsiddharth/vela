# RED-TEAM AUDIT — BTC KXBTC15M TWAP-endgame backtest

Date: 2026-06-09. Auditor: independent replication, adversarial.
Scripts: `backtest/analysis/audit_{leakage,proxy,integrity,takerev,oos}.py`.
All numbers reproduced from `data/*.parquet` via independent code (not the original scan).

## Verdict table

| Claim | Verdict | Numbers |
|---|---|---|
| (a) Binance 1s is a usable RTI proxy after causal trailing-24h de-bias, residual std ~$10 | **CONFIRMED** | resid std $10.4 @ lookback=96 (24h); $7.4 @ 6h. mean≈0. |
| (b) Lock-detection win rate at τ=30s, \|mhat\|>$50 = 99.97% | **CONFIRMED** | 1 flip / 3848 = 99.974%. Identical raw vs interpolated. |
| (c) Taker edge net-negative everywhere | **CONFIRMED (and strengthened)** | −0.55 to −1.10¢/contract; realistic ask-fill makes it WORSE. |
| Out-of-sample / regime stability | **CONFIRMED** | H1-tuned thresholds hold on H2 (99.6–100%). |
| Data integrity / timestamp alignment | **CONFIRMED** | 60/60 samples every window, 0 dups, alignment correct. |
| Proxy bias drift | **FRAGILE (known, mitigated)** | weekly median −$30→+$93; slow enough that 24h de-bias tracks it. |

No leakage found. The headline negative result is correct. There is **no capturable taker edge.**

---

## 1. Look-ahead / leakage — NO LEAKAGE

- **`interpolate(limit_direction="both")` leaks NOTHING here.** The Binance 1s
  klines are *complete* over the final 300s: of 1,890,900 cells, **0 are filled
  by interpolation**. Every decision column (τ∈{30,45,60,90,120}) has 100% raw
  coverage. Recomputing the entire lock-detection table on the **raw, un-filled**
  matrix gives *byte-identical* win rates (τ=30/$50 → 1 flip/3848 both ways).
  The caveat in FINDINGS is moot — the fill code never fires on this data.
  (It *would* be a latent bug on sparser data; harmless as-is.)
- **`causal_bias` is genuinely causal.** It matches a manual `shift(1).rolling`
  recompute exactly. A leaky centered (look-ahead) variant does **not** improve
  the win rate (99.974% either way), confirming the causal version isn't leaving
  free accuracy on the table — i.e. it's honest *and* near-optimal.
- **`estimate()`** uses only locked samples sec∈[τ,60] plus the current spot at τ
  as a martingale forecast for sec∈[1,τ). No window's own outcome or future
  sample enters. Verified.

## 2. Proxy validity — CONFIRMED, with one nuance

- **Bias level/drift:** overall mean +$21, median +$11, std $45. Weekly median
  swings **−$30 → +$93** over two months (real, large). BUT intra-day it is
  **slow**: hour-to-hour median change std=$5.3 (max $35); window-to-window
  std=$4.8. A 24h causal trailing median keeps up.
- **Residual depends on lookback** (the "~$10" is lookback-specific):
  2h→$5.9, 6h→$7.4, 24h(96)→$10.4, 48h→$12.6, 96h→$16.6 std. **Shorter is
  better here** — a 6–8 window (1.5–2h) lookback would *halve* the residual and
  is still causal. The chosen 24h is conservative, not optimal; not wrong.
- **No silent outcome flips from residual blow-up:** across lookbacks 8–192 the
  τ=30/$50 win rate stays 99.97–100%. The de-bias is robust to lookback choice.
- **Settlement IS a plain mean of 60, not trimmed.** Reconstructing settle from
  Binance final-60s and comparing to `true_settle` after de-bias: plain mean
  (resid std, post const-bias removal) **beats** 20%-trimmed and median60
  (44.681 < 44.711 < 45.003). Plain mean wins. Confirms Kalshi metadata.

## 3. Independent taker-EV replication — CONFIRMED NEGATIVE (worse with realism)

Re-derived from scratch with a realistic fill model: a taker buying the chosen
side pays that side's **actual taker-buy execution price** near τ (taker_side ==
chosen side), size-weighted. Fee = `ceil(0.07·P·(1−P)·100)` cents (round-UP).

Net EV (¢/contract) is **negative in every (τ, threshold) cell**, range −0.55
to −1.10. Crucially, the **realistic ask-fill is consistently MORE negative**
than the naive mean-fill (aggressors pay up): e.g. τ=30/$50 naive −0.92 vs ask
−1.06; τ=60/$75 naive −0.95 vs ask −1.09. The careful fill model does **not**
flip the sign — it confirms it harder.

Why: at the confident tail the winning side already trades at 0.985–0.999, so
gross edge (winrate − fill) is ~0 to +0.4¢ and frequently negative; the 1¢ fee
round-up swamps it. **Taker near-lock is dead. No edge to capture.**

## 4. Out-of-sample — CONFIRMED

- Split at 2026-05-06 12:00. Win rates by half are indistinguishable
  (τ=30/$50: H1 99.95%, H2 100.0%; τ=60/$50: H1 99.89%, H2 99.79%).
- **True OOS threshold test** (tune min threshold for ≥99.9% on H1, apply blind
  to H2): τ=30→thr 30 gives H2 100% (n=2375); τ=60→thr 60 gives H2 99.77%;
  τ=90→thr 80 gives H2 99.64%. The $50/$75 picks are **not look-ahead-tainted**
  — the de-bias is purely trailing, no global fit exists to overfit.
- Taker net EV negative in **both** halves, all cells.

## 5. Data integrity — CLEAN

- 6,308 markets; 6,303 have Binance (5 missing = 0.08%). Every covered window has
  **exactly 60** settlement samples in sec∈[1,60]; **0 duplicates**; prices
  in-range ($59,141–$82,805 vs settle $59,346–$82,669).
- **Timestamp alignment correct:** settlement window [1..60] gives the
  minimum/tied de-biased residual; [0..59]/[2..61] differ negligibly (smooth 1Hz
  data ⇒ off-by-one immaterial). No off-by-one inflation.
- **`true_settle` is the real settlement price:** 99.27% of windows have
  true_settle inside the de-biased per-second [min,max] band; `strike` == prior
  window's `true_settle` at 99.7% (median |diff| $0.00) — confirms ATM struck at
  prior settle.

## The one real flip (not a bug)
τ=30/$50's single miss: `KXBTC15M-26APR241145-45`, mhat −$51.1, true margin
+$4.49. The de-bias residual on that window exceeded $50 and flipped a near-ATM
call. Genuine model error, not leakage — exactly why $75 yields 0 flips.

## Bottom line
The pipeline is methodologically sound. No look-ahead. The proxy is valid; the
bias is large but slow and the causal de-bias handles it (and could be tightened
with a shorter lookback). The negative taker result is correct and robust — a
more careful, realistic fill model makes it *more* negative, not less. There is
**no taker edge**. Any positive strategy must avoid the taker fee structure
entirely (maker/limit provision), which this data set cannot prove out.

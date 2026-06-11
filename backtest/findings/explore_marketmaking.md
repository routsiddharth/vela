# Pure two-sided market-making / spread-capture on Kalshi crypto markets

Date: 2026-06-10. Reproduce: `cd bitcoin && source ../ingest/venv/bin/activate &&
PYTHONPATH=$(pwd) python backtest/analysis/explore_marketmaking.py`
(writes `backtest/analysis/mm_sweep.csv`). Deep-dive numbers in this doc were
produced by the inline snippets driving `simulate_market()` from the same module.

## VERDICT: NO (maybe, knife's-edge). Neutral spread-capture market-making is
**break-even-to-negative after adverse selection.** The only configs that "made
money" did so by *abandoning neutrality* — letting directional inventory run — and
that P&L was a coin-flip dominated by 2 of 14 markets (remove them → **-$512/day**).
When I impose real inventory discipline (true MM), the best cell is **+$168/day on
$50 with a bootstrap 5–95% CI of -$155 to +$449** — i.e. statistically
indistinguishable from zero on the available sample.

---

## 1. Data & method

- **Primary:** live Kalshi book+tape, `livepaper/data/paper.db` (3.42 h capture,
  all 4 series). `book_snaps` (1/s: real touch + queue depth) + `trades` (every
  print: `taker_side`, `yes_price`, `size`) + `windows` (real settlement).
  Settled markets usable: **14 KXBTC15M, 14 KXETH15M, 5 KXETHD, 0 KXBTCD**.
- **Scale cross-check:** `backtest/data/trades.parquet` — 2.44 M BTC15M prints
  across **2,497 settled markets**, final 180 s, joined to `markets.parquet`
  outcomes. Used to measure adverse-selection-by-time at scale (no book in it).
- **Fees:** modeled exactly — taker `max(ceil_cent(0.07·p(1-p)),0.01)`; maker
  **per-order** `ceil_cent(0.0175·qty·p(1-p))` amortized over qty (so a 10-lot
  maker order is ~0.04–0.05¢/ct, cheap). Maker is the relevant path here.

### Fill model (the honest part)
Book is bids-only; `yes_ask = 1 − best_no_bid`. Tape→maker mapping (verified
against the book, §below): `taker_side='no'` = taker sells YES → **fills my
resting YES bid** at `yes_price ≤ my_bid`; `taker_side='yes'` = taker buys YES →
**fills my resting NO bid** at `1−yes_price ≤ my_no_bid`. I rest one order per
side at the touch and sit **behind the full displayed queue** (`*_bid_sz`);
aggressive same-direction flow consumes the queue ahead of me first, then fills
me. Filled inventory is **marked to true settlement** (captures adverse
selection). Mapping check on a BTC15M market: 84.5% of `taker=yes` prints ≥
yes_ask, 79.5% of `taker=no` prints ≤ yes_bid (rest is 1 s snapshot lag). Sound.

---

## 2. What the book actually offers

| series   | avg spread | mode spread | touch queue (yes/no) | volume        |
|----------|-----------:|------------:|---------------------:|---------------|
| KXBTC15M | 0.89¢      | **1¢** (69%); 22% locked at 0¢ | 1813 / 1213 ct | ~2.9 M ct/h |
| KXETH15M | 1.39¢      | 1–2¢ (2¢ in 28%) | **177 / 172 ct** | ~100 k ct/h |
| KXETHD   | 3.39¢      | 1–3¢        | 1835 / 721 ct        | ~34 k ct/h    |

- **BTC15M is mostly a 1¢-spread, fat-queue book.** Joining a 1¢ spread leaves ~0¢
  to capture after you account for which side fills.
- **ETH15M has a thin queue (~175 ct)** that turns over ~570×/h → easy fills — and
  wider spreads — so it *looks* like the MM target. It isn't (§4).
- 22–25% of BTC15M snaps are **locked** (yes_bid+no_bid=100¢): zero capturable
  spread.

---

## 3. Adverse selection at scale (2,497 markets, decisive)

Fill-weighted, by time-to-close, on the parquet. `pY`,`pN` are the maker buy
prices my YES/NO bids fill at; "pair_cost"=`pY+pN` (a YES+NO pair settles to $1):

| sec-to-close | YES-bid win% / px | NO-bid win% / px | pair_cost | **pair edge** | vol |
|-------------:|-------------------|------------------|----------:|-------------:|----:|
| 120–180 | 50.3% / .507 | 51.1% / .507 | 1.014 | **−1.45¢** | 67 M |
| 60–120  | 50.4% / .510 | 52.2% / .524 | 1.034 | **−3.35¢** | 72 M |
| 30–60   | 56.3% / .547 | 54.5% / .564 | 1.111 | **−11.1¢** | 30 M |
| 15–30   | 63.8% / .613 | 58.0% / .589 | 1.202 | **−20.2¢** | 11 M |
| 0–15    | 64.1% / .633 | 57.6% / .592 | 1.225 | **−22.5¢** |  8 M |

**At the trade level, the two sides do NOT fill cheaply at the same time.** Flow is
one-directional: you fill the side the market is moving *toward* (hence avg fill px
> 0.50 and win-rate only modestly above px). The "pair_cost" of buying both via
the tape is **> $1.00 everywhere and blows out near close** — naive simultaneous
two-sided lifting *loses*, worst near resolution. This is textbook adverse
selection and it is monotone in time-to-close.

---

## 4. The live-book simulation — where the apparent edge comes from, and why it dies

Running the resting-quote sim on the live book (join touch, only quote when book
spread ≥ 2¢, stop quoting < 60 s to close), best series = **ETH15M**:

- Raw "full" P&L: **+$211.9 / 3.42 h = +$1,487/day** on $50. Looks great.
- **Decomposition:** paired spread = +$119.5 (12.7¢/pair, 941 pairs) ; **naked
  inventory = +$106.0** on 1,159 unpaired ct ; fees −$13.6. Only **62% of fills
  paired** — ~38% are naked directional bets.
- The 12.7¢/pair "spread" is **intra-window volatility harvesting**: the 15-min
  market oscillates around 0.50, so I buy YES on a dip *and* NO on a later dip and
  the pair still settles to $1. Real — **but only if price mean-reverts.** If it
  trends, I just accumulate the losing side (the naked inventory).

**Per-market distribution is the tell (rigorous time-ordered mark-to-settlement):**
mean **$15.13**, **median $0.32**, min −$40.63, max +$231.31. The mean is carried
entirely by **2 of 14 markets**; remove the top 2 → **−$512/day**. Bootstrap over
the 14 markets: $/day mean $1,481 but **5th pct −$777**. This is a near-zero-median,
fat-tailed *directional* outcome, not a robust spread.

### Kill test — impose inventory discipline (be an actual neutral MM)
Cap naked skew at N contracts (stop quoting the heavy side), ETH15M, spr≥2¢, stop60:

| inv cap | net $/day | per-mkt median | % markets positive |
|--------:|----------:|---------------:|-------------------:|
| 20 (neutral) | **−$123** | −$1.54 | 43% |
| 50           | −$156     | −$0.58 | 50% |
| 100          | +$32      | +$0.32 | 50% |
| ∞ (uncapped) | +$1,487   | +$0.32 | 50% |

**The instant you enforce neutrality, the edge vanishes (−$123/day).** The entire
positive number was the uncapped directional gamble. ETH15M neutral MM **loses.**

### Best neutral cell across all series (bootstrap 5–95% CI on per-market net)
| series · config (neutral, cap 20) | net $/day | 5–95% CI | fills/day | n mkts |
|---|---:|---|---:|---:|
| **KXBTC15M · spr≥2¢** | **+$168** | **−$155 … +$449** | 2,407 | 14 |
| KXETHD (hourly ladder) · spr≥1¢ | +$29 | −$61 … +$110 | 1,847 | 5 |
| KXETH15M · spr≥2¢ | −$123 | −$355 … +$115 | 11,919 | 14 |

The single not-obviously-dead cell is **BTC15M, quote only when book spread ≥ 2¢,
stay neutral** — median market +$3.19, 57% positive. But spread ≥ 2¢ is only ~10%
of BTC15M time, and the CI straddles zero. BTC15M at spread≥1¢ (the other 90% of
the time) is **−$877/day neutral** — you must NOT quote the 1¢ book.

---

## 5. Why it fails (one line)

The spread you can *see* (1–2¢) is not the spread you *capture*: maker fills are
adversely selected so the two sides fill one-at-a-time as price moves, average buy
price exceeds fair value, and a true *neutral* book runs −1 to −3¢/ct after fees —
worse near resolution. The only "profit" is letting inventory run, which is a
directional coin-flip (median market ≈ $0, P&L set by 2 lucky windows of 14).

---

## STRUCTURED SUMMARY

**STRATEGY (best survivable config):** Rest two-sided 10-lot maker bids at the
touch on **KXBTC15M only when the book spread ≥ 2¢**; buy YES at best_yes_bid and
NO at best_no_bid; stop quoting < 60 s to close; **hard inventory cap ±20 ct**
(stop quoting the heavy side). Sit behind full displayed queue. (Neutral; no
directional view.)

**BACKTEST:** Live Kalshi book+tape+settlement, 3.42 h, 14 BTC15M / 14 ETH15M / 5
ETHD settled markets; resting-quote fill sim walking the real trade tape with
queue-ahead consumption; inventory marked to true settlement. Scale
adverse-selection cross-check on 2,497 BTC15M markets / 2.44 M prints.

**RESULTS (best neutral cell, BTC15M spr≥2¢, cap 20):**
- Spread captured/round-trip: ~2–4¢ gross *when paired*, but only ~38–62% of fills
  pair; net spread after adverse selection ≈ **0 to +1¢/ct**.
- Fills/day: ~2,400 ct (only in the ~10% of time spread ≥ 2¢).
- Win-rate on filled inventory: **~52–58%** (near fair; paired legs net ~$1).
- Avg $/round-trip after fees+adverse-sel: **≈ +$0.03–0.07/pair, ≈ 0 median.**
- **$/day on $50: +$168 point estimate, 95% CI −$155 … +$449** (straddles 0).
  ETH15M neutral: **−$123/day**. Uncapped "+$1,487/day" is a directional artifact
  (median market $0.32; −$512/day without its top 2 of 14 markets).
- Max drawdown: per-market losers to −$40; capped run dd ≈ $20–30 per losing
  cluster. Capacity: tiny — ETH15M touch queue ~175 ct, BTC15M spread≥2¢ is rare;
  10-lots are absorbable but the spread≥2¢ windows are thin.

**HITS $5/DAY?:** **Maybe, low-confidence.** Point estimate clears it (+$168/day on
BTC15M spr≥2¢), but the 5–95% CI is −$155…+$449 and the median market is +$3, so a
realistic live result could easily be $0 or negative. ETH15M (the high-volume
candidate) is a clear **NO** when run neutrally. Bankroll $50 is sufficient
capital-wise (resting capital ~$10–20); the constraint is edge, not bankroll.

**CONFIDENCE: LOW.** Top risk: **live fill rate & queue priority vs sim.** The sim
sits behind displayed queue but assumes I get filled once it clears and that the
favorable spread≥2¢ snapshots are *fillable* at my bid; in reality (a) other makers
join the same thin level, (b) the spread≥2¢ moments may be exactly when nobody
crosses, and (c) maker queue position is worse than modeled. All bias the live
number **below** the sim. Sample is also only 14 BTC15M markets.

**VERDICT:** Pure neutral spread-capture is break-even after adverse selection;
the only way it prints is by abandoning neutrality (a directional coin-flip), so it
is not a reliable $5/day on $50 — the one survivable cell (BTC15M, spread≥2¢,
inventory-capped) has a point estimate of +$168/day but a confidence interval that
straddles zero.

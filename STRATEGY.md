# Strategy — the endgame "already-decided" trade

*Full change log below. Current live params in `livepaper/config.py`. Research and
backtests in `backtest/findings/`.*

---

## The core idea

Kalshi's short-dated crypto markets settle on the **simple mean of 60 CF-Benchmarks
RTI samples over the final 60 seconds**, not the last-second price. An average is
sluggish: once ~45 of the 60 samples are banked, the remaining 15 can barely move
the result. The outcome is mathematically settled — but the market keeps trading as
if it's live.

Naive traders watch *last price*. When spot lurches the wrong way in the final
minute, they panic and dump the soon-to-win side cheap. The average has already
drowned out that move. **The edge is fading that panic.**

You are not predicting Bitcoin. You're better at arithmetic than a scared person
staring at a flickering price.

---

## Change log

### v0 — Original hypothesis (pre-backtest, ~2026-06-01)

**What:** Buy the near-locked winning side as a *taker* once the model is confident
the outcome is decided. Simple: identify near-certain outcomes, pay the ask, collect
$1.

**Why:** The 60-sample averaging process was correctly identified as creating near-
certain outcomes before the close. The original write-up assumed you could capture
the spread between fair value and a slightly discounted market price.

**Result: DEAD.** Backtested on 6,308 settled KXBTC15M windows (Apr–Jun 2026).
Net **−0.5 to −0.95¢/contract everywhere.** When the model is confident, BTC is
visibly far from the strike, so the crowd already prices the winner at 0.985–0.999.
There is no discount left. Kalshi's quadratic fee rounds up to 1¢ at those extremes
and buries the sub-cent gross gap. The market is efficient with respect to spot.

---

### v1 — Panic-fade: first correct formulation (2026-06-09)

**What:** Don't buy calmly — buy from someone panicking. Rest a cheap bid on the
TWAP-favored side and let a frightened seller hit it. Only fill a print in the
price band `[0.55, 0.97]`. Gate on raw margin `|m̂| ≥ $40–50` at τ=45s.

A six-subagent search (fees, tail risk, sizing, frequency, market-making, strategy
book) converged on the correct framing:
- `p_side = Φ(|margin| / sd_S)` — model P(our side wins), folding in both the
  diffusion of unlocked samples AND the de-bias tracking error (~$10–16 std)
- Price floor at 0.55 — below the floor the market is correctly telling you you're
  wrong (adverse selection, not panic)
- Maker role — fee is 16× cheaper than taker at these prices
- Binance→RTI de-bias: causal trailing-24h median, residual std ~$10, essential

**Why:** The original taker path was dead. The 60-sample lock *does* create an edge
but it's only accessible when a panic seller shows up and dumps a winner cheap.
You're the maker catching their fear, not the taker chasing a locked price.

**Result (backtest):** 0 losing windows in 2 months. OOS-positive. Positive every
month (Apr +4.7¢, May +3.1¢, Jun +3.2¢). Net +3 to +10¢/contract depending on
CAP. ~78% of the cheap volume was genuine panic sells (takers dumping the winner
into bids). The lock sets the win rate; the panic sets the entry price.

**Initial live params after this:** `P_SIDE_MIN=0.99, WIN_PX_FLOOR=0.55, CAP=0.97,
TAU=45, SEC=[5,45]`

**Key risk identified:** The maker fill rate was completely unproven. A backtest
can't tell you whether you'll win the queue for cheap orders, how fast the edge
decays, or how it holds through a USDT/RTI basis shock. The whole build order was:
prove edge offline → paper-test fill rate → risk real money.

---

### v2 — Robust optimization: gate lowered, CAP raised, floor lowered (2026-06-10 to 2026-06-13)

Three distinct optimization runs over the full parameter space (`opt_harness.py`,
2500-window BTC backtest, OOS = H2):

**2a — Robust gate optimization (`opt_robust.md`)**

Found that `P_SIDE_MIN=0.99` sits safely above a cliff at 0.986/0.987. Below 0.986
a single window flips and worst-case crashes to ~−$5. The frontier is shallow but
the current 0.99 is confirmed robust. Floor was safely lowered `0.55 → 0.45`
(admits a few more cheap windows, zero robustness cost: +$0.004/day OOS, +1.6%).

**2b — Maximum-$/day optimization (`opt_maxday.md`)**

Full 6-D grid search found `P_SIDE_MIN=0.97` as a genuine local optimum: +22% full
sample, +73% OOS vs 0.99 baseline. SEC_LO raised `1 → 5` slightly improved
min-month. CAP at 0.99 beats 0.98 (more fills, same quality).

**2c — Frequency optimization (`opt_frequency.md`)**

The real finding: **`P_SIDE_MIN=0.84` is a separate, better local optimum.**
At 0.84, OOS more than doubles (0.217 → 0.504 $/day), windows +69% (166 → 280),
every month still positive. A cliff sits at 0.814 — one tick looser and OOS goes
negative. The 0.90–0.96 band is a trap: OOS drops, one month goes negative. The
safe plateau is `[0.82, 0.84]`. Importantly, 0.84 beats both 0.97 and 0.99 on OOS
while trading more often. The high gate was leaving good fades on the table.

**Why:** At 0.99 the model only fires on near-mathematical-certainties (≈$50+
margin). Lowering to 0.84 captures windows with meaningful but not overwhelming
confidence, which happens to be where real panic supply lives. The 0.97 zone is a
trap — OOS reverses sign in a flipped train/test split.

**Changes made:**
- `P_SIDE_MIN: 0.99 → 0.84` (swarm-optimized; 96.6% win vs 100%, takes real
  ~−$5 losing windows but doubles OOS $/day)
- `WIN_PX_FLOOR: 0.55 → 0.45` (safe; no cliff on this axis)
- `CAP: 0.97 → 0.99` (confident flow prints at 0.985–0.99, 0.97 caught ~nothing;
  0.99 captures it, +1.94¢/ct & ~5x volume, p_side gate still sets win rate)
- `SEC_LO: 5 → 1` (free extra volume at the tight gate, still locked)

**Result (live, paper period):** bot went live paper on BTC. Fills started coming
in at the predicted cadence. Gate firing more often as expected.

---

### v3 — Per-asset split + ETH live (2026-06-13 to 2026-06-16)

**What:** Split BTC and ETH into independent bots (`VELA_ASSET=BTC/ETH`) with
separate data dirs, DBs, logs, and per-process risk guards (env-overridable
`VELA_MAX_DAILY_LOSS`, `VELA_MAX_OPEN_NOTIONAL`, `VELA_POSITION_USD`).

Introduced **`P_SIDE_MIN_BY_ASSET = {"BTC": 0.84, "ETH": 0.98}`** — per-asset gate
overrides instead of a shared gate.

**Why ETH gets a stricter gate (0.98):** ETH is ~2× fatter-tailed than BTC (excess
kurtosis 39 vs 21, Apr–Jun 2026). The Gaussian `p_side` model is overconfident in
the marginal band for ETH — live calibration showed it's only honest at ≥0.98. BTC
stays 0.84 (calibrated-to-conservative there). Analysis: 2026-06-13.

**Both bots went real-money live.** ETH had been running paper for ~2 days (+$6.4
paper). Old paper data archived to `data_eth_paper_bak_*/` on switchover (2026-06-16).

ETH launched with tighter per-bot limits: daily-loss halt $15 (vs $25 BTC), open-
notional cap $15 (vs $25 BTC). Both bots share one Kalshi account; guards are
per-process only (no cross-bot global halt).

**Result (as of 2026-06-17):**
- BTC: +$12.34 realized (running since 2026-06-13, started at ~$20 balance)
- ETH: +$1.59 realized (running since 2026-06-16)
- ETH fires rarely due to 0.98 gate — most windows don't clear it

---

### v4 — Strong-take pathway added (2026-06-14)

**What:** A second, fully independent live pathway running *alongside* the panic-
fade (not replacing it). When `VELA_STRONG_TAKE=1`, on **KXBTC15M only**: if a
side's ask reaches ≥0.95 with `sec_to_close ∈ [2, 45)`, send one *taker* buy on
that side, $5 notional, hold to settlement. Ignores `p_side` entirely.

Separate order book (`strong_orders` / `strong_fills` in `LiveExecutor`), separate
fill routing by `order_id` (since both pathways can have orders on the same ticker
simultaneously). Shares kill-switch, daily-loss halt, and open-notional cap with
the panic-fade.

**Why:** Modeled on 14h of live KXBTC15M tape (`model_095.py`): ~+2.4¢/contract,
100% win. The thesis is the inverse of the panic-fade — instead of catching a
panicked SELLER of the winner at a discount, this catches a near-certain winner
where the MARKET itself is pricing it at ≥0.95 (confident, not panicked). The
0.95+ zone is where settlement is basically locked and the last few takers are
buying certainty. Fee is taker rate (0.07 × p × (1−p)), low at high prices.

Hourly series (KXBTCD) was explicitly excluded — modeled negative near the money
(near-money ladder strikes flip), raising the threshold to 0.97 made it worse
(same windows, worse prices).

**Result (live, through ~2026-06-17, ~24+ windows):**
Strong-take is firing every BTC 15-min window where a side hits 0.95+ ask. Early
live results: ~95% win rate but real PnL slightly negative (≈ −$1 to −$2 cumulative,
≈ −1 to −2¢/ct) — one full-stake flip erased the penny wins, exactly the predicted
tail dynamic. Sample still too small to judge (24 windows). The 14h model had a
tiny sample (11–16 windows); this is the live measurement that matters.

---

## Current live configuration (2026-06-17)

| Param | BTC | ETH | Notes |
|-------|-----|-----|-------|
| `P_SIDE_MIN` | 0.84 | 0.98 | per-asset gate |
| `WIN_PX_FLOOR` | 0.45 | 0.45 | adverse-selection floor |
| `CAP` | 0.99 | 0.99 | genuine-discount cap |
| `TAU_DECISION` | 45s | 45s | lock point |
| `SEC_LO / SEC_HI` | 1 / 45 | 1 / 45 | fill window |
| `POSITION_USD` | $5 | $5 | fixed notional/window |
| `LIVE_MAX_DAILY_LOSS` | $25 | $15 | per-bot halt |
| `LIVE_MAX_OPEN_NOTIONAL` | $25 | $15 | per-bot cap |
| Strong-take | ON (0.95 threshold, KXBTC15M) | OFF | separate pathway |

Markets: KXBTC15M + KXBTCD (BTC), KXETH15M + KXETHD (ETH).

---

## Standing risks

- **Close calls are the trap.** The whole edge depends on staying away from coin-
  flip windows. The p_side gate is the wall.
- **Sudden jumps are the tail risk.** A near-locked outcome pays pennies; a rare
  violent move against it costs the full stake. Survival is about sizing, not
  win rate.
- **The strong-take edge is small-sample.** 14h model had 11–16 windows. Live is
  measuring it now. Do not size up until it has 100+ live windows.
- **Capacity is modest.** A few dollars/day at best, bursty. Clusters on panic
  days since panic is the raw material.
- **ETH gate (0.98) fires rarely.** Most windows don't clear it. ETH PnL will be
  sparse; judge it over weeks, not days.

---

## Rule: updating this file

**Whenever a strategy parameter changes or a new pathway is added, add an entry
to the change log above with:**
1. What changed (param name, old value → new value, or new pathway description)
2. Why (the data, backtest, or live observation that motivated it)
3. Result (backtest numbers if pre-live; live PnL / win rate once it has data)

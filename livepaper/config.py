"""Live paper-trader config — multi-market TWAP-anchored panic-fade forward test.

Trades several Kalshi crypto series that ALL settle the same way (the simple mean
of 60 CF-Benchmarks RTI samples over the final 60s — confirmed from rules_primary
for every series). The strategy is unchanged; only the asset/strike differ:
  KXBTC15M  up/down, ATM (strike = prior 60s avg)     15-min   BTC/BRTI
  KXETH15M  up/down, ATM                               15-min   ETH/ERTI
  KXBTCD    above/below, fixed floor strike (`greater`) 1-hour  BTC/BRTI
  KXETHD    above/below, fixed floor strike (`greater`) 1-hour  ETH/ERTI
The two-sided "range" series (KXBTC/KXETH) are intentionally excluded — their
two-boundary margin doesn't fit the single-margin model the edge was validated on.
"""
from __future__ import annotations
import os
from pathlib import Path

# ---- asset selection (split BTC / ETH into independent bots) -----------------
# VELA_ASSET=BTC or ETH runs a single-asset bot in its own data dir (data_btc/
# data_eth), so the two trade independently with separate bankrolls/DBs/logs.
# Unset => legacy combined run in data/ (both assets, one shared bankroll).
ASSET = os.environ.get("VELA_ASSET", "").upper() or None


def _envf(name: str, default: float) -> float:
    """Per-process float override from env (empty/unset/bad => default). Lets each
    asset's bot run with its OWN budget/stop-loss without a shared config edit."""
    v = os.environ.get(name, "")
    try:
        return float(v) if v != "" else default
    except ValueError:
        return default

# ---- where data lands -------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA = ROOT / (f"data_{ASSET.lower()}" if ASSET else "data")
DB_PATH = DATA / "paper.db"
SHARED_PORTFOLIO_DB = ROOT / "data_shared" / "portfolio.db"
RAW_KALSHI = DATA / "raw_kalshi.jsonl"
RAW_BINANCE = DATA / "raw_binance.jsonl"
LOG_PATH = DATA / "run.log"
RAW_DUMP = False

# ---- markets to trade -------------------------------------------------------
# Each: series ticker, asset key, Binance symbol (1s spot proxy for that RTI),
# and `band`. asset groups the per-asset Binance feed + de-bias (BTCUSDT->BRTI,
# ETHUSDT->ERTI); both BTC series share one BTC feed/de-bias (same index basis).
# `band`: for ATM up/down series (one market per event) leave None — admit it.
# For FIXED-STRIKE LADDER series (KXBTCD has ~100 strikes/hour spanning ±15%),
# only the strikes within `band` (fraction of spot) are ever near-the-money where
# the panic-fade applies; the rest are deep ITM/OTM noise. 0.004 = +/-0.4%.
_ALL_MARKETS = [
    {"series": "KXBTC15M", "asset": "BTC", "symbol": "BTCUSDT", "band": None},
    {"series": "KXETH15M", "asset": "ETH", "symbol": "ETHUSDT", "band": None},
    {"series": "KXBTCD",   "asset": "BTC", "symbol": "BTCUSDT", "band": 0.004},
    {"series": "KXETHD",   "asset": "ETH", "symbol": "ETHUSDT", "band": 0.004},
]
# when VELA_ASSET is set, trade only that asset's series (both its 15M + hourly)
MARKETS = [m for m in _ALL_MARKETS if ASSET is None or m["asset"] == ASSET]
# unique assets -> Binance symbol (for the feed) and the 15M series to bootstrap
# that asset's de-bias from (15M settles every 15 min => lots of calibration data).
ASSET_SYMBOL = {m["asset"]: m["symbol"] for m in MARKETS}
ASSET_DEBIAS_SERIES = {"BTC": "KXBTC15M", "ETH": "KXETH15M"}

SETTLE_SECS = 60                # settlement = mean of the 60 RTI samples before close
EST_BUFFER_SECS = 400           # seconds of price history kept in RAM per symbol

# ---- strategy: CONFIDENCE-gated panic fade (multi-agent search v2) -----------
# Old rule gated on |margin| >= THR_BPS*price and capped price SEPARATELY -> a
# cheap print (e.g. 0.28) at a thin margin (+$30) cleared the gate and we bought
# the market screaming we're wrong = adverse selection, not panic. Three agents
# converged: gate on the MODEL PROBABILITY our side wins (folds in the estimate's
# own uncertainty sd_S), not a raw $ margin. p_side>=0.99 removed ALL losing
# windows in a 2-month backtest (right-skewed PnL) and is the unit-free form of
# the robust ~$40-50 margin gate. Plus a price FLOOR (real panic prints stay >0.55;
# below that the market is confidently correct) and a genuine-discount CAP.
TAU_DECISION = 45               # lock the bet side at sec_to_close == 45 (the lock cliff)
SEC_HI = 45                     # actionable window: take fills while sec_to_close in
SEC_LO = 1                      #   [SEC_LO, SEC_HI]  (1, not 5 -> free extra volume, still locked)
P_SIDE_MIN = 0.84               # GATE: model P(our side wins) >= this. Swarm-optimized
                                #   (opt_harness): 0.84 ~4x's the edge vs 0.99 ($1.63 vs
                                #   $0.43/day full) and is OOS-robust in BOTH split directions
                                #   (0.95-0.97 was a trap — OOS reversed sign). 96.6% win, so
                                #   it DOES take real ~-$5 losing windows (vs never at 0.99).
                                #   Sits well above the 0.814 OOS cliff.
# Per-asset gate override (falls back to P_SIDE_MIN for any asset not listed). ETH is
# ~2x fatter-tailed than BTC (excess kurtosis 39 vs 21, Apr-Jun) so its Gaussian p_side
# is overconfident in the marginal band; live calibration shows it's only honest at
# >=0.98. BTC stays 0.84 (calibrated-to-conservative there). Per-asset analysis 2026-06-13.
P_SIDE_MIN_BY_ASSET = {"BTC": 0.84, "ETH": 0.98}
WIN_PX_FLOOR = 0.45             # reject cheap adverse-selection prints (only fade real panic)
CAP = 0.99                      # only buy the winning side at price <= CAP. 0.99 (not 0.97):
                                #   the confident flow prints at 0.985-0.99 (market agrees), so
                                #   0.97 caught ~nothing; 0.99 captures it, stays 100%-win (the
                                #   p_side gate sets the win rate), +1.94c/ct & ~5x volume under
                                #   maker fees (A1 $/day-max). Floor 0.55 still blocks the trap.
CAP_FRAC = 1.0                  # fraction of each panic print we assume we capture

# variance model for p_side = norm_cdf(|margin_hat| / sd_S):
#   sd_S^2 = sigma_sec^2 * remaining_var_factor(n_rem)   (diffusion of unlocked samples)
#          + proxy_sd^2                                   (causal de-bias tracking std)
SIGMA_LOOKBACK = 120            # secs of 1s closes used to estimate per-second price std
SIGMA_FALLBACK_BPS = 1.0        # pre-warmup sigma_sec = this/1e4 * price (USD/sec)
PROXY_SD_FALLBACK_BPS = 1.5     # pre-bootstrap proxy_sd = this/1e4 * price (USD)

# ---- sizing: fixed-fraction — 10% of current portfolio per trade ------------
# Per-window position = round_UP(PORTFOLIO_FRACTION * portfolio_value / price) whole
# contracts (Kalshi trades integer contracts). "Portfolio" = cash + open-position
# cost, so the bet COMPOUNDS with the balance (grows as you win, shrinks as you
# lose). One flip costs ~10% of portfolio, so this leans on the p_side>=0.99 gate.
# Q_BLEND_MODEL is the EV guard: only fill when the blended prob (0.55 model + 0.45
# market price) exceeds the price you'd pay.
PORTFOLIO_FRACTION = 0.10
MIN_WINDOW_NOTIONAL = 5.0       # HARD FLOOR: every trade deploys >= $5 (rounds UP to whole
                               #   contracts). At the $50 start 10% == $5, so trades are $5
                               #   and grow with the account; never smaller. One ~-$5 flip
                               #   window = ~10% of bank — sized for the 96.6%-win gate.
Q_BLEND_MODEL = 0.55

# ---- fees: we REST bids -> we are the MAKER, fee rounds up per ORDER ---------
MAKER_FEE_RATE = 0.0175         # maker fee: round_up_cent(MAKER_FEE_RATE * C * P * (1-P))

# ---- paper bankroll ---------------------------------------------------------
# $50 paper account. Per-trade size is 10% of the CURRENT portfolio (see sizing),
# so it scales up as you win and down as you lose. One flip ~ 10% of portfolio, so
# survival rests on the p_side>=0.99 gate.
BANKROLL = 50.0                     # starting cash (USD), shared across all markets
MAX_WINDOW_NOTIONAL = 1_000_000.0   # absolute hard ceiling only; live size = 10% of portfolio

# ---- LIVE trading (REAL money) ---------------------------------------------
# OFF unless VELA_LIVE=1. When on, the executor places REAL Kalshi maker orders
# instead of simulating fills off the tape. Live order size is
# PORTFOLIO_FRACTION of the shared live risk ledger in SHARED_PORTFOLIO_DB.
# Guarded by a kill switch + daily-loss halt. See live_exec.py.
LIVE = os.environ.get("VELA_LIVE", "") == "1"
LIVE_DEMO = os.environ.get("VELA_LIVE_DEMO", "") == "1"   # use Kalshi demo cluster

# ---- migration shadow mode (MIGRATION_PLAN.md Phase 1) ----------------------
# VELA_SHADOW=1 makes the engine ALSO compute the new PriceBlend.price()+projection
# path each tick and log any divergence from the live _estimate path to the
# `shadow_diff` table. Trading stays on the OLD path — this only observes. Off by
# default; opt in when restarting the bots to collect the N-day parity evidence
# that gates the Phase-1 cutover.
SHADOW = os.environ.get("VELA_SHADOW", "") == "1"
LIVE_REST_FLOOR = WIN_PX_FLOOR  # never rest a buy below this (adverse-selection guard)
LIVE_REST_CAP = CAP             # never rest a buy above this (no-discount guard)
# resting price: join the favored side's best bid (be the maker a panic seller hits),
# clamped to [floor, cap]. The one real execution knob — tune after seeing fill rate.
LIVE_JOIN_BEST_BID = True
# risk guards
LIVE_MAX_DAILY_LOSS = _envf("VELA_MAX_DAILY_LOSS", 25.0)      # stop-loss: halt + cancel-all at this day loss (per bot)
LIVE_MAX_OPEN_NOTIONAL = _envf("VELA_MAX_OPEN_NOTIONAL", 25.0)  # absolute floor for total resting+open exposure cap
LIVE_MAX_OPEN_FRACTION = _envf("VELA_MAX_OPEN_FRACTION", 0.50)  # cap total resting+open exposure as % of shared ledger
LIVE_KILL_FILE = "KILL"         # presence of this file in the data dir => cancel-all + halt
LIVE_CANCEL_BEFORE_CLOSE = 2    # cancel any unfilled remainder at sec_to_close <= this

# ---- ALT pathway: "strong take" (taker on near-certain favorites) -----------
# A SECOND, INDEPENDENT live pathway that runs ALONGSIDE the panic-fade (it does
# NOT replace or disable it). When VELA_STRONG_TAKE=1, on STRONG_SERIES only: if a
# side's ASK >= STRONG_TAKE_THRESH while STRONG_TAKE_SEC_LO <= sec_to_close <
# STRONG_TAKE_SEC_HI, send ONE *taker* buy on that side (crossing the spread, up to
# STRONG_MAX_PX), sized from the shared live risk ledger, hold to settlement. Ignores p_side
# entirely. Its book is kept SEPARATE from the panic-fade's per-window MarketState
# accounting (so it can't corrupt it), but it shares the SAME real account and the
# SAME kill-switch / daily-loss / open-notional guards. Off => totally inert, so
# the ETH bot and any default run are unaffected. Modeled (14h, KXBTC15M): ~+2.4c/ct.
STRONG_TAKE = os.environ.get("VELA_STRONG_TAKE", "") == "1"
STRONG_SERIES = {"KXBTC15M"}    # 15-min ATM only (the branch that modeled positive)
STRONG_TAKE_THRESH = 0.95       # take a side once its ask reaches this
STRONG_MAX_PX = 0.99            # never pay more than this to take (>=1.00 can't profit)
STRONG_TAKE_SEC_HI = 45.0       # only act while sec_to_close < this (the "<45s" rule)
STRONG_TAKE_SEC_LO = 2.0        # ...and >= this (leave room for the taker fill to land)
STRONG_TAKER_FEE_RATE = 0.07    # taker fee (crossing); only a fallback if the fill omits fee_cost

# ---- de-bias (causal Binance->RTI) ------------------------------------------
DEBIAS_LOOKBACK = 96            # trailing windows (~24h of 15M) for the median bias
DEBIAS_BOOTSTRAP = 160          # settled windows to seed each asset's bias at startup

# ---- hosts ------------------------------------------------------------------
KALSHI_REST = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_WS = "wss://api.elections.kalshi.com/trade-api/ws/v2"
KALSHI_WS_PATH = "/trade-api/ws/v2"
BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="   # combined stream
BINANCE_REST = "https://data-api.binance.vision"

# ---- cadences (seconds) -----------------------------------------------------
TICK = 1.0
DISCOVERY_EVERY = 20.0
SETTLE_POLL_EVERY = 30.0

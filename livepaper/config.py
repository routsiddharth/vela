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
from pathlib import Path

# ---- where data lands -------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "paper.db"
RAW_KALSHI = DATA / "raw_kalshi.jsonl"
RAW_BINANCE = DATA / "raw_binance.jsonl"
LOG_PATH = DATA / "run.log"
RAW_DUMP = True

# ---- markets to trade -------------------------------------------------------
# Each: series ticker, asset key, Binance symbol (1s spot proxy for that RTI),
# and `band`. asset groups the per-asset Binance feed + de-bias (BTCUSDT->BRTI,
# ETHUSDT->ERTI); both BTC series share one BTC feed/de-bias (same index basis).
# `band`: for ATM up/down series (one market per event) leave None — admit it.
# For FIXED-STRIKE LADDER series (KXBTCD has ~100 strikes/hour spanning ±15%),
# only the strikes within `band` (fraction of spot) are ever near-the-money where
# the panic-fade applies; the rest are deep ITM/OTM noise. 0.004 = +/-0.4%.
MARKETS = [
    {"series": "KXBTC15M", "asset": "BTC", "symbol": "BTCUSDT", "band": None},
    {"series": "KXETH15M", "asset": "ETH", "symbol": "ETHUSDT", "band": None},
    {"series": "KXBTCD",   "asset": "BTC", "symbol": "BTCUSDT", "band": 0.004},
    {"series": "KXETHD",   "asset": "ETH", "symbol": "ETHUSDT", "band": 0.004},
]
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

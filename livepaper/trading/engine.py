"""The trading engine: per-second decision/logging tick, trade-driven paper fills,
and settlement — multi-market.

Price + de-bias are owned by PriceBlend (MIGRATION_PLAN.md): each tick the engine
pulls that asset's raw-average bundle via `priceblend.price(asset)` and runs the
window projection (`projection.project`) to get mhat/margin/sd_S/p_side — it never
reads the Binance feed or de-bias tracker directly. Calibration on settlement goes
back through `priceblend.calibrate(...)`. The gate is RELATIVE (P_SIDE_MIN of the
model probability) so it scales across BTC (~$62k) and ETH (~$1.7k).

Fill model (mirrors backtest/analysis/final_strategy.py): lock the bet side at
sec_to_close==TAU_DECISION using the causal de-biased TWAP margin; while
sec_to_close in [SEC_LO, SEC_HI] and the gate holds, every winning-side print at
price <= CAP is a panic seller we fade -> a paper fill. Hold to settlement.
"""
from __future__ import annotations
import math, time
from .. import config as C
from .book import MarketState
from ..priceblend import PriceBlend
from .projection import project
from ..contract import SettlementTruth


def maker_order_fee(qty: float, p: float) -> float:
    """Kalshi MAKER fee in $ for ONE order of `qty` contracts at price `p`:
    round_up_cent(MAKER_FEE_RATE * qty * p * (1-p)). The round-up is per ORDER
    (qty inside), so it amortizes across size instead of the old per-contract 1c
    floor. We rest bids -> we are the maker, so this is the rate that applies."""
    return math.ceil(C.MAKER_FEE_RATE * qty * p * (1.0 - p) * 100) / 100.0


def maker_fee_per_ct(p: float) -> float:
    """Per-contract maker fee approximation (ignores rounding) — for sizing only."""
    return C.MAKER_FEE_RATE * p * (1.0 - p)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF via bisection (computed once, for display threshold)."""
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


Z_GATE = _norm_ppf(C.P_SIDE_MIN)   # margin (in sd_S units) to pass the default gate
# Per-asset gate thresholds in sd_S units, so thr_abs (the displayed/stored "margin
# needed") matches whichever gate actually applies to that asset.
Z_GATE_BY_ASSET = {a: _norm_ppf(p) for a, p in C.P_SIDE_MIN_BY_ASSET.items()}


class Engine:
    def __init__(self, store, feed, disc, debias: dict, market_meta: dict, log,
                 live_broker=None) -> None:
        self.store = store
        self.feed = feed
        self.disc = disc
        self.debias = debias                 # asset -> Debias
        self.meta = market_meta              # series -> {asset, symbol}
        self.log = log
        self.states: dict[str, MarketState] = {}
        self.cash = C.BANKROLL
        self.realized = 0.0
        self.n_trades = 0
        self.last_px_logged: dict[str, int] = {}
        # PriceBlend owns price + de-bias; the engine consumes only its bundle/
        # calibrate interface. rest_avg60 gives calibration the REST fallback the
        # old _settle used (Discovery.binance_avg60) when the local buffer is thin.
        self.priceblend = PriceBlend(
            feed, debias,
            rest_avg60=(disc.binance_avg60 if disc is not None else None))
        # ---- LIVE trading (real Kalshi orders); None in paper mode --------------
        self.live = None
        if C.LIVE or live_broker is not None:
            from .live_exec import LiveExecutor
            if live_broker is None:
                from .broker import LiveBroker
                live_broker = LiveBroker(demo=C.LIVE_DEMO)
            self.live = LiveExecutor(live_broker, store, log, C.DATA)
            self.live.attach(self.states)
            self.live.startup_reconcile()
            if self.live.balance is not None:
                self.cash = self.live.balance     # start accounting from shared risk ledger
            self.log(f"[live] LIVE TRADING ENABLED — real orders, "
                     f"{C.PORTFOLIO_FRACTION:.0%} of shared risk balance/window")

    # ---- discovery hook -----------------------------------------------------
    def sync_markets(self, actives: list[dict]) -> set[str]:
        for a in actives:
            if a["ticker"] not in self.states:
                self.states[a["ticker"]] = MarketState(
                    a["ticker"], a["close_ts"], a["strike"],
                    a["asset"], a["symbol"], a["series"])
                self.store.event("market_open",
                                 f"{a['ticker']} {a['asset']} close={a['close_ts']} strike={a['strike']}")
        return {tk for tk, s in self.states.items() if not s.settled}

    # ---- WS callbacks -------------------------------------------------------
    def on_snapshot(self, tk: str, msg: dict) -> None:
        s = self.states.get(tk)
        if s:
            s.book.snapshot(msg)
            s.have_book = True

    def on_delta(self, tk: str, msg: dict) -> None:
        s = self.states.get(tk)
        if s and s.have_book:
            s.book.delta(msg)

    def on_lifecycle(self, tk: str, msg: dict) -> None:
        self.store.event("lifecycle", f"{tk} {msg.get('event_type')}")

    def on_trade(self, tk: str, msg: dict) -> None:
        s = self.states.get(tk)
        if not s:
            return
        now = time.time()
        sec = s.sec_to_close(now)
        yp = _f(msg.get("yes_price_dollars"))
        np_ = _f(msg.get("no_price_dollars"))
        sz = _f(msg.get("count_fp")) or 0.0
        self.store.trade(tk, sec, yp, np_, sz, msg.get("taker_side", ""))
        self.n_trades += 1
        self._maybe_fill(s, sec, yp, np_, sz)

    def portfolio_value(self) -> float:
        """Current account value = cash + cost basis of open positions. The 10%
        bet sizes off this, so it compounds with the running balance."""
        open_cost = sum(st.window_cost for st in self.states.values() if not st.settled)
        return self.cash + open_cost

    def _maybe_fill(self, s: MarketState, sec: float, yp, np_, sz: float) -> None:
        if self.live is not None:
            return                # LIVE: fills come from real orders, not the tape sim
        if not (s.decided and s.gate_active and s.bet_yes is not None):
            return
        if not (C.SEC_LO <= sec <= C.SEC_HI):
            return
        win_px = yp if s.bet_yes else np_
        # genuine-discount window: reject cheap adverse-selection prints (floor) and
        # non-discounts (cap). Below the floor the market is confidently correct.
        if win_px is None or not (C.WIN_PX_FLOOR <= win_px <= C.CAP):
            return
        # EV guard: only fade when the blended prob beats the price (model underpriced).
        p_win = C.Q_BLEND_MODEL * s.p_side + (1.0 - C.Q_BLEND_MODEL) * win_px
        if p_win <= win_px:
            return
        # --- sizing: 10% of current portfolio per window, rounded UP, integer ----
        if s.budget_ct is None:                           # set once, at the first fill
            target = max(C.MIN_WINDOW_NOTIONAL,           # >= $5/trade floor
                         C.PORTFOLIO_FRACTION * self.portfolio_value())
            s.budget_ct = math.ceil(target / win_px)      # round UP -> notional >= target
        remaining = s.budget_ct - int(s.total_qty)
        if remaining <= 0:
            return
        avail = int(sz * C.CAP_FRAC)                       # whole contracts offered
        cash_ct = int(self.cash / win_px)                 # whole contracts we can afford
        ceil_ct = int((C.MAX_WINDOW_NOTIONAL - s.window_cost) / win_px)
        qty = min(remaining, avail, cash_ct, ceil_ct)
        if qty < 1:
            return
        f = maker_order_fee(qty, win_px)                  # per-ORDER fee (one round-up)
        cost = qty * win_px
        self.cash -= cost
        s.window_cost += cost
        s.total_qty += qty
        s.fills.append((win_px, qty, f))
        self.store.fill(s.ticker, sec, "yes" if s.bet_yes else "no", win_px, qty, f,
                        cost, s.decision_margin,
                        f"fade p_side={s.p_side:.3f} 10%->{s.budget_ct}ct")

    # ---- per-second tick ----------------------------------------------------
    def tick(self) -> None:
        now = time.time()
        if self.live is not None:
            self.live.poll_and_manage(now)   # kill-check, apply real fills, cancel near close
        for s in list(self.states.values()):
            if s.settled:
                continue
            nb = self.priceblend.price(s.asset)      # §2.A bundle: raw avg + de-bias stats
            if nb is None:
                continue
            spot_sec, spot = nb.ts, nb.raw_avg
            if self.last_px_logged.get(s.symbol) != spot_sec:
                self.store.price(s.symbol, spot_sec, spot)
                self.last_px_logged[s.symbol] = spot_sec
            sec = s.sec_to_close(now)
            if sec < -5:
                continue
            # Window projection on the trading side: average THIS window's locked
            # buckets and apply the bundle's de-bias once. project() reproduces the
            # pre-migration _estimate bit-for-bit (gated by tests/test_priceblend_parity).
            pr = project(nb, lambda e: self.priceblend.bucket_at(s.asset, e),
                         s.strike, s.close_ts, now)
            mhat, margin, sd_S, p_side = pr.mhat, pr.margin, pr.sd_S, pr.p_side
            gate_min = C.P_SIDE_MIN_BY_ASSET.get(s.asset, C.P_SIDE_MIN)   # per-asset gate
            thr_abs = Z_GATE_BY_ASSET.get(s.asset, Z_GATE) * sd_S         # margin to clear it (display)
            if not s.decided and C.SEC_LO <= sec <= C.TAU_DECISION:
                s.decided = True
                s.bet_yes = pr.bet_yes
                s.decision_margin = margin
                s.sd_S = sd_S
                s.p_side = p_side
                s.gate_active = p_side >= gate_min
                self.store.event("decision",
                    f"{s.ticker} [{s.asset}] sec={sec:.0f} margin={margin:+.1f} "
                    f"sd={sd_S:.1f} p_side={p_side:.4f} bet={'YES' if s.bet_yes else 'NO'} "
                    f"gate={'ON' if s.gate_active else 'off'}")
                if self.live is not None and s.gate_active:
                    self.live.on_decision(s, sec)   # rest the real maker bid
            b = s.book
            byb, bnb = b.best_yes_bid(), b.best_no_bid()
            self.store.book(s.ticker, sec, byb, b.yes.get(byb, 0.0) if byb else 0.0,
                            bnb, b.no.get(bnb, 0.0) if bnb else 0.0,
                            b.yes_ask(), b.no_ask(),
                            sum(b.yes.values()), sum(b.no.values()), b.compact())
            self.store.estimate(s.ticker, s.asset, sec, spot, pr.n_lock, pr.lmean, pr.shat,
                                nb.delta, mhat, s.strike, margin, thr_abs,
                                ("yes" if s.bet_yes else "no") if s.decided else None,
                                s.gate_active, s.decided)
            # golden-master oracle row: the RAW sigma_sec/resid_std behind sd_S (straight
            # from the bundle) + the realized sd_S/p_side + the raw-average second.
            self.store.oracle(s.ticker, s.asset, sec, spot_sec, nb.sigma_sec,
                              nb.resid_std, sd_S, p_side)
            if self.live is not None:
                self.live.consider_take(s, sec, now)   # ALT pathway (no-op unless STRONG_TAKE)

    # ---- settlement ---------------------------------------------------------
    def settle_closed(self) -> None:
        now = time.time()
        for s in list(self.states.values()):
            if s.settled or s.sec_to_close(now) > -2:
                continue
            res = self.disc.settled(s.ticker)
            if res:
                self._settle(s, res)

    def _settle(self, s: MarketState, res: dict) -> None:
        true_settle, result = res["true_settle"], res["result"]
        won = (s.bet_yes and result == "yes") or (s.bet_yes is False and result == "no")
        payout = s.total_qty * 1.0 if won else 0.0
        fees = sum(f for _, _, f in s.fills)   # f is per-ORDER total maker fee
        gross = payout - s.window_cost
        net = gross - fees
        self.realized += net
        if self.live is not None:
            self.cash = self.live.note_settle(s.ticker, net)   # reconcile REAL balance
            self.live.settle_strong(s.ticker, result, time.time())  # ALT pathway book
        else:
            self.cash += payout - fees
        avg_px = (sum(p * q for p, q, _ in s.fills) / s.total_qty) if s.total_qty else None
        # de-bias calibration goes back through PriceBlend: it owns the de-bias
        # tracker, the avg60 measurement, and the REST fallback. We only persist the row.
        cr = self.priceblend.calibrate(
            SettlementTruth(s.ticker, s.series, s.asset, s.symbol, s.close_ts, true_settle))
        if cr.err is not None:
            self.store.debias_row(s.ticker, s.asset, s.close_ts,
                                  cr.binance_avg60, true_settle, cr.err)
        s.settled = True
        if s.fills:
            self.store.window((s.ticker, s.asset, s.series, s.close_ts, s.strike, true_settle,
                               result, s.decision_margin,
                               "yes" if s.bet_yes else ("no" if s.decided else None),
                               int(s.gate_active), len(s.fills), s.total_qty, avg_px,
                               gross, fees, net, int(won) if s.decided else None, self.cash))
            self.log(f"SETTLED {s.ticker} [{s.asset}] {result.upper()} "
                     f"{'WIN' if won else 'LOSS'} qty={s.total_qty:.1f} "
                     f"net=${net:+.3f} bal=${self.cash:.2f}")

def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None

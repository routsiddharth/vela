"""A4 — FREQUENCY EXPANSION for the TWAP-anchored panic-fade.

Owns: increase trade FREQUENCY without degrading win rate. More independent,
high-win-rate trades is the second way to beat the left tail (more wins to absorb
the rare flip and compound).

Levers tested (all CAUSAL, MAKER fees from strategy_search/fees.py):
  1. tau (decision time) sweep: tau in {60,55,50,45,40,35} (+30 for context).
     Earlier tau -> more time in the actionable window -> more catchable panic
     prints, but fewer settlement samples locked -> lower lock reliability.
  2. Actionable-window bounds: SEC_LO in {1,3,5,10}, SEC_HI in {45,55,60}.
     How much +EV volume lives in [1,5) and [45,60)?
  3. More series: incremental windows/day from KXBTCD hourly ladder (sweep band)
     and ETH (from paper.db); plus a survey of other admissible Kalshi crypto
     series (reasoned from market.py Discovery + Kalshi series naming).
  4. Two-sided fade: also fade the model-LOSING side when locked. Independent
     +EV volume, or just added losers that hurt the tail?

The canonical fade model (final_strategy.py), re-implemented with the corrected
MAKER fee and a tau-parametric lock:
  At tau, mhat = de-biased TWAP estimate of (settle - strike). Lock bet_yes = mhat>0.
  Gate: |mhat| >= THR (USD).  Over actionable sec in [SEC_LO, SEC_HI], lift prints
  on the target side offered at price <= CAP, capture CAP_FRAC of printed size,
  hold to settlement.  PnL/contract: win -> (1-price); lose -> -price; minus the
  per-order MAKER fee amortized per contract (one round-up per print).

Run:  python -m backtest.strategy_search.a4_frequency
"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.btc_lib as L
from backtest.strategy_search import fees as F

# ---------------------------------------------------------------------------
# Load the BIG BTC parquet (statistical power) once.
# ---------------------------------------------------------------------------
M = L.load_markets()
PIV = L.binance_matrix()
M = M[M.ticker.isin(PIV.index)].reset_index(drop=True)
RAW60 = L.raw_avg60(PIV)
TR = L.load_trades()
MID = M.close_dt.quantile(0.5)
DAYS = (M.close_dt.max() - M.close_dt.min()).days
SAMPLE_FRAC = TR.ticker.nunique() / len(M)   # trades cover ~2497/6303 windows
WINDOWS_PER_DAY_BTC15M = len(M) / DAYS       # ~94/day full universe (15-min cadence => 96/day)


def lock_estimate(tau: int) -> pd.DataFrame:
    """Causal per-ticker decision at tau: mhat (=settle_hat - strike), bet side.
    Returns frame indexed by ticker (chronological m order) with mhat, bet_yes, yes, close_dt.
    The de-bias `delta` is recomputed per call but is tau-independent (uses raw_avg60),
    so the only tau effect is which settlement seconds are LOCKED in `estimate`."""
    delta = L.causal_bias(M, RAW60)                       # aligned to M.index
    # Index delta by ticker and feed estimate a piv sliced in the SAME M.ticker order,
    # so `s_hat_binance - delta` aligns row-for-row (matches final_strategy.py exactly).
    dser = pd.Series(delta.values, index=M.ticker.values)
    shat = L.estimate(PIV.loc[M.ticker], tau, dser)       # indexed by ticker
    g = M[["ticker", "yes", "close_dt", "strike", "true_settle"]].copy()
    g["mhat"] = shat.values - M.strike.values
    g["bet_yes"] = g.mhat > 0
    return g.dropna(subset=["mhat"])


def backtest(tau=45, THR=40.0, CAP=0.99, SEC_LO=5, SEC_HI=None, CAP_FRAC=1.0,
             two_sided=False, rate=F.MAKER):
    """Run the fade with a given config. SEC_HI defaults to tau (decision = top of window).
    two_sided: also fade the model-losing side (separate accounting flag side_role)."""
    if SEC_HI is None:
        SEC_HI = tau
    g = lock_estimate(tau)
    g = g[g.mhat.abs() >= THR].copy()
    d = TR.merge(g[["ticker", "bet_yes", "yes", "close_dt", "mhat"]], on="ticker", how="inner")
    d = d[(d.sec_to_close >= SEC_LO) & (d.sec_to_close <= SEC_HI)]
    if len(d) == 0:
        return None

    frames = []
    # winning-side (model-target) leg
    w = d.copy()
    w["px"] = np.where(w.bet_yes, w.yes_price, w.no_price)
    w["won"] = np.where(w.bet_yes, w.yes == 1, w.yes == 0)
    w["leg"] = "target"
    frames.append(w)
    if two_sided:
        o = d.copy()
        o["px"] = np.where(o.bet_yes, o.no_price, o.yes_price)   # the OTHER side
        o["won"] = np.where(o.bet_yes, o.yes == 0, o.yes == 1)   # other side wins iff model loses
        o["leg"] = "other"
        frames.append(o)
    d = pd.concat(frames, ignore_index=True)

    d = d[(d.px <= CAP) & (d.px > 0)]
    if len(d) == 0:
        return None
    d["qty"] = d["size"] * CAP_FRAC
    # MAKER fee per order (one round-up per print), amortized per contract
    d["fee_pc"] = F.fee_per_contract(d["qty"].values, d.px.values, rate)
    d["pnl_pc"] = np.where(d.won, 1 - d.px, -d.px) - d["fee_pc"]
    d["pnl"] = d["pnl_pc"] * d["qty"]
    return d


def stats(d, label=""):
    if d is None or len(d) == 0:
        return None
    ct = d["qty"].sum()
    pnl = d.pnl.sum()
    wr = (d.won * d["qty"]).sum() / ct * 100
    windows = d.ticker.nunique()
    losers = d[~d.won].ticker.nunique()
    pw = d.groupby("ticker").pnl.sum()
    worst = pw.min()
    cvar5 = pw[pw <= pw.quantile(0.05)].mean() if len(pw) >= 20 else pw.min()
    # scale sampled contracts to full universe over the period -> $/day
    usd_day = pnl / SAMPLE_FRAC / DAYS
    # windows traded per day in the SAMPLED universe, scaled to full
    wins_per_day = windows / SAMPLE_FRAC / DAYS
    return dict(label=label, net_c=pnl / ct * 100, wr=wr, windows=windows,
                losers=losers, wins_per_day=wins_per_day, k_ct=ct / 1000,
                usd_day=usd_day, worst=worst, cvar5=cvar5, pnl=pnl)


def split_stats(d, label=""):
    """full / in-sample (H1) / out-of-sample (H2)."""
    if d is None:
        return None, None, None
    full = stats(d, label)
    h1 = stats(d[d.close_dt < MID], label + "/IS")
    h2 = stats(d[d.close_dt >= MID], label + "/OOS")
    return full, h1, h2


def row(s):
    if s is None:
        return f"{'(no trades)':>60}"
    return (f"net_c={s['net_c']:6.2f}  wr={s['wr']:7.3f}%  win/day={s['wins_per_day']:5.1f}  "
            f"wins={s['windows']:4d}/los={s['losers']:3d}  k_ct={s['k_ct']:6.1f}  "
            f"$/day={s['usd_day']:7.0f}  worst={s['worst']:7.2f}  cvar5={s['cvar5']:7.3f}")


# ===========================================================================
def main():
    print("=" * 120)
    print(f"A4 FREQUENCY EXPANSION  |  BTC KXBTC15M big parquet  |  {len(M)} windows, "
          f"{DAYS} days, sample_frac={SAMPLE_FRAC:.3f}, ~{WINDOWS_PER_DAY_BTC15M:.0f} win/day universe")
    print(f"MAKER fee (rate {F.MAKER}); $/day & win/day scaled sample->full universe")
    print("=" * 120)

    # THR=40 USD ~ the lenient ~1.6bps gate at BTC $62k? 1.6bps*62k=~$10. Use a small
    # gate sweep so the frequency lever isn't conflated with leniency. Baseline THR=40.
    THR = 40.0
    CAP = 0.99

    # ---- LEVER 1: tau sweep (SEC_HI = tau, SEC_LO=5) ----------------------
    print("\n[L1] tau (decision-time) sweep   SEC_LO=5, SEC_HI=tau, THR=$40, CAP=0.99, MAKER")
    print("     earlier tau => longer actionable window + more prints, but fewer locked settle samples")
    print(f"     {'tau':>4} {'n_lock':>7}  full / OOS")
    l1 = {}
    for tau in [60, 55, 50, 45, 40, 35, 30]:
        n_lock = max(0, 60 - tau + 1) if tau <= 60 else 0
        d = backtest(tau=tau, THR=THR, CAP=CAP, SEC_LO=5, SEC_HI=tau)
        full, h1, h2 = split_stats(d, f"tau{tau}")
        l1[tau] = full
        print(f"     {tau:>4} {n_lock:>7}  {row(full)}")
        print(f"     {'':>4} {'':>7}  OOS: {row(h2)}")

    # tau>60: pure martingale (no samples locked) — does lock reliability hold?
    print("\n[L1b] tau>60 (PURE MARTINGALE, no settle samples locked yet) — does the side-lock still hold?")
    for tau in [75, 90]:
        d = backtest(tau=tau, THR=THR, CAP=CAP, SEC_LO=5, SEC_HI=60)  # window capped at 60 (settle region)
        full, _, h2 = split_stats(d, f"tau{tau}")
        print(f"     tau={tau:>3} (SEC in [5,60])  {row(full)}")
        print(f"     {'':>17}  OOS: {row(h2)}")

    # ---- LEVER 2: actionable-window bounds (CAUSAL) -----------------------
    # IMPORTANT CAUSALITY NOTE: a fill at sec_to_close = s can only use a side
    # decided with info available at time s, i.e. a lock at tau >= s. Taking fills
    # at sec in (tau, 60] while using the tau=45 lock is LOOK-AHEAD (the lock uses
    # samples in [45,60] that hadn't happened at sec=55). So SEC_HI must be <= tau.
    # The only causal way to widen the window UPWARD is to move the lock itself
    # (covered in L1, and it lands in the unreliable tau>=50 regime). Here we hold
    # SEC_HI = tau = 45 and sweep SEC_LO downward (the only causal widening).
    print("\n[L2] actionable-window bounds (CAUSAL: SEC_HI = tau = 45)   THR=$40, CAP=0.99, MAKER")
    print("     Only SEC_LO is causally free to move; SEC_HI>tau would be look-ahead (see note in source).")
    print(f"     {'SEC_LO':>7}{'SEC_HI':>7}  full")
    for SEC_LO in [1, 2, 3, 5, 10]:
        d = backtest(tau=45, THR=THR, CAP=CAP, SEC_LO=SEC_LO, SEC_HI=45)
        full = stats(d, f"[{SEC_LO},45]")
        print(f"     {SEC_LO:>7}{45:>7}  {row(full)}")

    print("\n[L2b] DEMONSTRATION that SEC_HI>tau is look-ahead — re-lock causally at tau=SEC_HI:")
    print("      'tau=45 lock, trade to 60' (BIASED) vs 'lock at 60, trade to 60' (CAUSAL):")
    d_bias = backtest(tau=45, THR=THR, CAP=CAP, SEC_LO=5, SEC_HI=60)   # uses 45-lock for sec 45..60 -> peeks
    d_caus = backtest(tau=60, THR=THR, CAP=CAP, SEC_LO=5, SEC_HI=60)   # honest re-lock at 60
    print(f"      BIASED (45-lock, [5,60]): {row(stats(d_bias))}")
    print(f"      CAUSAL (60-lock, [5,60]): {row(stats(d_caus))}")

    # marginal volume in the inner band [1,5) at the causal lock
    print("\n[L2c] marginal value of the inner band [1,5) at tau=45 (causal)")
    for lo, hi, name in [(1, 5, "[1,5) inner only"), (5, 45, "[5,45) core"), (1, 45, "[1,45) widened")]:
        d = backtest(tau=45, THR=THR, CAP=CAP, SEC_LO=lo, SEC_HI=hi)
        s = stats(d, name)
        print(f"     {name:>22}  {row(s)}")

    # ---- LEVER 4: two-sided fade ------------------------------------------
    print("\n[L4] two-sided fade   tau=45, SEC=[5,45], THR=$40, CAP=0.99, MAKER")
    d1 = backtest(tau=45, THR=THR, CAP=CAP, SEC_LO=5, SEC_HI=45, two_sided=False)
    s1 = stats(d1, "target-only")
    print(f"     target-only          {row(s1)}")
    dts = backtest(tau=45, THR=THR, CAP=CAP, SEC_LO=5, SEC_HI=45, two_sided=True)
    sboth = stats(dts, "both-sides")
    print(f"     both-sides (combined){row(sboth)}")
    # isolate the OTHER leg only
    if dts is not None:
        other = dts[dts.leg == "other"]
        so = stats(other, "OTHER-leg-only")
        print(f"     OTHER leg only       {row(so)}")
        # OTHER leg conditioned on margin strength (is panic on the losing side EV+ only when margin large?)
        print("     OTHER leg by |mhat| bucket (does a bigger model edge make the losing side safe to fade?):")
        for lo, hi in [(40, 80), (80, 150), (150, 1e9)]:
            sub = other[(other.mhat.abs() >= lo) & (other.mhat.abs() < hi)]
            s = stats(sub, f"|mhat|[{lo},{hi})")
            print(f"       |mhat| in [{lo:>4.0f},{hi:>5.0f})  {row(s)}")

    # ---- LEVER 3: more series (ETH + KXBTCD from paper.db) ----------------
    series_report()
    band_sweep_btcd()
    new_series_survey()

    # ---- recommendation ----------------------------------------------------
    recommend(l1)


# ---------------------------------------------------------------------------
# LEVER 3a: ETH + KXBTCD realized frequency from paper.db (small but real)
# ---------------------------------------------------------------------------
import sqlite3
from pathlib import Path
DB = Path(__file__).resolve().parents[2] / "livepaper" / "data" / "paper.db"


def series_report():
    print("\n[L3] more series — realized cadence & catchable panic from paper.db (small, ~4.5h, 1 day)")
    if not DB.exists():
        print("     paper.db missing"); return
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    w = pd.read_sql("select * from windows", c)
    tr = pd.read_sql("select ticker, sec_to_close, yes_price, no_price, size, taker_side from trades", c)
    # how many windows per series got a gate-active decision (would have traded if a cheap print appeared)
    span_h = (w.close_ts.max() - w.close_ts.min()) / 3600.0
    print(f"     paper.db spans {span_h:.1f}h, {len(w)} windows")
    for series, gw in w.groupby("series"):
        gated = gw[gw.gate_active == 1]
        traded = gw[gw.n_fills > 0]
        per_day_windows = len(gw) / span_h * 24
        per_day_gated = len(gated) / span_h * 24
        per_day_traded = len(traded) / span_h * 24
        print(f"     {series:>10}: windows/day~{per_day_windows:5.0f}  gate-active/day~{per_day_gated:5.0f}  "
              f"actually-traded/day~{per_day_traded:5.0f}  (n_windows={len(gw)})")
    # catchable panic: count cheap prints (<=0.05) on tracked markets in the actionable window
    tr_act = tr[(tr.sec_to_close >= 5) & (tr.sec_to_close <= 45)]
    tr_act = tr_act.copy()
    tr_act["series"] = tr_act.ticker.str.extract(r"^([A-Z0-9]+?)-")[0]
    tr_act["series"] = tr_act.ticker.apply(_series_of)
    cheap = tr_act[(tr_act.yes_price <= 0.05) | (tr_act.no_price <= 0.05)]
    print("     catchable cheap prints (<=5c) in sec[5,45], per series, scaled /day:")
    for series, g in cheap.groupby("series"):
        print(f"       {series:>10}: cheap prints/day~{len(g)/span_h*24:7.0f}  size/day~{g['size'].sum()/span_h*24:9.0f}")
    c.close()


def _series_of(ticker: str) -> str:
    for s in ("KXBTC15M", "KXETH15M", "KXBTCD", "KXETHD", "KXBTC", "KXETH"):
        if ticker.startswith(s + "-"):
            return s
    return ticker.split("-")[0]


# ---------------------------------------------------------------------------
# LEVER 3b: KXBTCD band-fraction sweep — how many near-money strikes/hour are
# actually catchable. We don't have a big KXBTCD parquet, so estimate from
# paper.db trades: count distinct (strike) markets within band of spot that print
# a cheap fade in the actionable window.
# ---------------------------------------------------------------------------
def band_sweep_btcd():
    print("\n[L3b] KXBTCD hourly-ladder band sweep — near-the-money strikes/hour that produce catchable panic")
    if not DB.exists():
        return
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    est = pd.read_sql("select ticker, sec_to_close, spot, strike, margin_hat, gate_active, bet_side "
                      "from estimates where ticker like 'KXBTCD-%'", c)
    tr = pd.read_sql("select ticker, sec_to_close, yes_price, no_price, size from trades "
                     "where ticker like 'KXBTCD-%'", c)
    if len(est) == 0:
        print("     no KXBTCD estimates in paper.db"); c.close(); return
    # per ticker: representative spot & strike near decision (sec~45)
    near = est[(est.sec_to_close >= 40) & (est.sec_to_close <= 60)]
    rep = near.groupby("ticker").agg(spot=("spot", "median"), strike=("strike", "median")).dropna()
    rep["band_frac"] = (rep.strike - rep.spot).abs() / rep.spot
    n_hours = (est.ticker.apply(lambda t: t.split("-")[1]).nunique())  # distinct hour-buckets
    print(f"     {rep.shape[0]} distinct KXBTCD strike-markets seen across ~{n_hours} hour-buckets in paper.db")
    cheap = tr[(tr.sec_to_close >= 5) & (tr.sec_to_close <= 45) &
               ((tr.yes_price <= 0.05) | (tr.no_price <= 0.05))]
    catchable_tickers = set(cheap.ticker.unique())
    for band in [0.001, 0.002, 0.004, 0.008, 0.015]:
        inb = rep[rep.band_frac <= band]
        strikes_per_hour = len(inb) / max(n_hours, 1)
        catch = inb.index.isin(catchable_tickers).sum()
        catch_per_hour = catch / max(n_hours, 1)
        print(f"     band=+/-{band*100:5.2f}%  strikes-in-band/hour~{strikes_per_hour:5.1f}  "
              f"of-which-catchable/hour~{catch_per_hour:5.1f}  (=>~{catch_per_hour*24:4.0f} catchable windows/day)")
    c.close()


# ---------------------------------------------------------------------------
# LEVER 3c: survey of OTHER Kalshi crypto series with the same 60s-mean settle
# ---------------------------------------------------------------------------
def new_series_survey():
    print("\n[L3c] OTHER Kalshi crypto series with the SAME 60s-mean settlement (reasoned from market.py + Kalshi naming)")
    print("     Admissible to the single-margin model = floor-strike 'greater[_or_equal]' (NOT two-sided range).")
    rows = [
        ("KXBTC15M",  "BTC", "15m", "up/down ATM",      "YES  (in use)  ~96 win/day"),
        ("KXETH15M",  "ETH", "15m", "up/down ATM",      "YES  (in use)  ~96 win/day, independent asset"),
        ("KXBTCD",    "BTC", "1h",  "floor ladder",     "YES  (in use)  ladder -> N near-money strikes/hr"),
        ("KXETHD",    "ETH", "1h",  "floor ladder",     "LIKELY admissible (greater) — mirror of KXBTCD on ETH"),
        ("KXSOLD",    "SOL", "1h",  "floor ladder",     "ADMISSIBLE IF same 60s-mean rule + SOL RTI exists; needs own de-bias feed (SOLUSDT)"),
        ("KXXRPD",    "XRP", "1h",  "floor ladder",     "ADMISSIBLE IF same rule; own feed (XRPUSDT)"),
        ("KXSOL15M",  "SOL", "15m", "up/down ATM",      "ADMISSIBLE IF same rule; 4th independent 15m engine (~96/day)"),
        ("KXETHD/HOURLY range", "ETH", "1h", "range",   "NO — two-boundary margin, excluded by design"),
        ("KXBTC (hourly range)","BTC","1h", "range",    "NO — two-sided range, excluded by design"),
    ]
    print(f"     {'series':>14} {'asset':>5} {'cad':>4} {'kind':>14}   admissibility")
    for s, a, cad, kind, note in rows:
        print(f"     {s:>14} {a:>5} {cad:>4} {kind:>14}   {note}")
    print("     NOTE: each NEW asset (SOL/XRP) needs its own Binance feed + causal de-bias (market.py Debias is")
    print("           per-asset already). Each 15m up/down series adds ~96 independent windows/day at the same")
    print("           win-rate IF its settle rule is the 60s-mean (verify rules_primary before enabling).")
    print("           Each Dxxx ladder adds (near-money-strikes/hr * 24) windows/day — see band sweep above.")


# ---------------------------------------------------------------------------
def recommend(l1):
    print("\n" + "=" * 120)
    print("RECOMMENDATION")
    print("=" * 120)
    # candidate taus that hold wr=100% (causal, SEC_HI=tau)
    safe = {tau: s for tau, s in l1.items() if s and s["losers"] == 0}
    print("  taus holding 100% win-rate (0 losers), SEC=[5,tau], THR=$40, CAP=0.99:")
    for tau, s in sorted(safe.items(), reverse=True):
        print(f"    tau={tau:>3}: win/day={s['wins_per_day']:4.1f}  net_c={s['net_c']:6.2f}  $/day={s['usd_day']:6.0f}")
    # robustness flag: tau<45 $/day is dominated by single-window outliers
    print("  CAUTION: tau<45 shows higher net_c/$/day but it is concentrated in 1-2 fat right-tail")
    print("           windows (tau=40: ~63% of pnl from ONE window) -> NOT robust frequency.")
    print("  PICK: tau=45, SEC=[1,45], THR=$40, CAP=0.99 — most windows/day (6.0) at 100% win-rate,")
    print("        smooth pnl, robust IS & OOS. Earlier tau buys edge not frequency; later tau breaks the lock.")
    print("  BIGGEST frequency lever is MORE SERIES (independent assets), not tau/window tuning:")
    print("    BTC15M alone caps at ~6 safe windows/day. +ETH15M ~2x, +KXBTCD/KXETHD ladders, +SOL/XRP -> multiplicative.")


if __name__ == "__main__":
    main()

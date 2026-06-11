"""A3 — Sizing / bankroll geometry for the BTC panic-fade strategy.

CORE THESIS
-----------
The live run is left-skewed because sizing is ~flat: we capture CAP_FRAC of each
print up to a fixed $/window notional, so we win pennies on ~90c stakes and a single
full-stake flip erases many wins. The fix is to size each fill by its EDGE and FLIP
RISK so that:
  * cheap + thin-margin prints (high flip prob q) get tiny / zero size,
  * fat-margin winners get more size,
and the realized PnL distribution shifts from left-skewed to right-skewed while the
geometric growth rate E[log(1+r)] (the right objective for compounding survival) rises.

THE PER-CONTRACT BET (buy the model-winning side at price p, hold to settle):
  win  (prob p_win): +(1-p) - fee_pc
  lose (prob q=1-p_win): -p - fee_pc
With net odds b = (1-p)/p, the Kelly fraction of bankroll on this bet is
  f* = (p_win*b - q) / b = p_win - q/b = p_win - q*p/(1-p).
We use p_win = calibrated P(our side wins), q = 1 - p_win (from fade_lib.model_pwin,
recalibrated — see CALIBRATION below).

SIZING RULES COMPARED (same gated fills, causal, MAKER fees):
  1. flat        — baseline: CAP_FRAC of print, capped at PER_WIN notional (current).
  2. kelly       — full Kelly f* on (p_win, p).
  3. kelly_half  — 1/2 Kelly  (realistic given model error).
  4. kelly_qtr   — 1/4 Kelly.
  5. cvar        — size so worst-case window loss <= CVAR_BUDGET of bankroll.
  6. edgeprop    — size proportional to expected net c/contract, zero below a floor.

Each rule produces a per-window dollar stake; we walk the bankroll forward window by
window (compounding) under per-window notional + cash constraints and report:
  total return, geometric growth E[log(1+r)], Sharpe, max drawdown, 5% CVaR /
  worst-window, and PnL-distribution skew. Plus: how each rule sizes the known live
  losing fill (@0.28, thin margin -> high q).

Run:  python -m backtest.strategy_search.a3_sizing
"""
from __future__ import annotations
import numpy as np, pandas as pd
from scipy.stats import norm
import backtest.btc_lib as L
import backtest.analysis.fade_lib as F
from backtest.strategy_search.fees import order_fee_vec, MAKER, TAKER, ZERO

# ----- operating point (mirror final_strategy / config) ----------------------
TAU = 45            # decision time, seconds-to-close
SEC_LO = 5          # actionable seconds window [SEC_LO, TAU]
CAP = 0.99          # only lift the winning side at price <= CAP
THR = 10.0          # |margin_hat| gate in USD (~ config THR_BPS≈$10) — KEEPS losers
                    # in the set so sizing has a left tail to reshape. THR=50 in the
                    # raw sample has 0 losers (it is already a perfect tail-killer but
                    # only 96 windows); the realistic live regime is the lower gate.
CAP_FRAC = 0.25     # competition haircut: we capture this frac of a print
PER_WIN_FLAT = 5.0  # flat baseline per-window notional cap ($)
BANKROLL0 = 200.0   # starting bankroll ($) — mid of the $50-$1000 range
MAX_WIN_FRAC = 0.25 # hard cap: never stake > this frac of bankroll on one window
CVAR_BUDGET = 0.02  # cvar rule: worst-case single-window loss <= 2% of bankroll
EDGE_FLOOR_C = 0.3  # edgeprop: zero size below 0.3c expected net/contract
EDGE_SCALE = 30.0   # edgeprop: $ per (c/contract) of edge, before caps
KELLY_CAL = True    # use the recalibrated + market-blended p_win for Kelly
# q-blend: the flip prob must respect BOTH the model AND the market price. A cheap
# print on a confident model is adverse selection (market knows). We blend the model
# win-prob with the market-implied win-prob (= the price we pay) so a thin-margin
# cheap fill gets a high q and tiny Kelly. W_MODEL is the weight on the model.
W_MODEL = 0.55


# =============================================================================
# 1. Build the gated fill set (one aggregate "winning-side fill" per window)
# =============================================================================
def build_fills(rate=MAKER, thr=THR):
    """Return per-window frame of the aggregate winning-side liftable volume at tau,
    with model p_win, price p, available qty, outcome, fee-per-contract, and chrono order.
    One row per window = the unit we size."""
    m = L.load_markets()
    piv = L.binance_matrix()
    m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
    sig = F.estimate_sigma_sec(piv)
    mp = F.model_pwin(piv.loc[m.ticker], m, TAU, sig)          # indexed by ticker
    mp = mp.dropna(subset=["p_yes", "margin_hat"]).reset_index()
    # gate on model margin magnitude
    g = mp[mp.margin_hat.abs() >= thr].copy()
    g["bet_yes"] = g.margin_hat > 0
    # model prob OUR side wins
    g["p_win_raw"] = np.where(g.bet_yes, g.p_yes, 1.0 - g.p_yes)

    # liftable volume + price on the winning side over [SEC_LO, TAU]
    tr = L.load_trades()
    tr = tr[(tr.sec_to_close >= SEC_LO) & (tr.sec_to_close <= TAU)]
    d = tr.merge(g[["ticker", "bet_yes"]], on="ticker", how="inner")
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px <= CAP) & (d.win_px > 0)]
    # aggregate per window: size-weighted avg price, total liftable size
    agg = d.groupby("ticker").apply(
        lambda x: pd.Series({
            "p": np.average(x.win_px, weights=x["size"]),
            "avail_qty": x["size"].sum(),
        }), include_groups=False).reset_index()

    f = g.merge(agg, on="ticker", how="inner")
    f["won"] = np.where(f.bet_yes, f.yes == 1, f.yes == 0)
    f = f.sort_values("close_dt").reset_index(drop=True)
    f["fee_pc"] = order_fee_vec(1.0, f.p.values, rate)   # ~per-contract fee proxy
    f["q_raw"] = 1.0 - f.p_win_raw
    # market-implied win-prob for the side we BUY: the price we pay IS the market's
    # probability our side wins. p_win_mkt = p.
    f["p_win_mkt"] = f.p
    return f, mp


# =============================================================================
# 2. Calibration of p_win (q must be calibrated for Kelly)
# =============================================================================
def calibrate(mp):
    """Reliability table of raw model p_yes vs realized, plus a monotone recalibration
    map (isotonic-style binned) so q is honest. Returns (reliability_df, recal_fn)."""
    d = mp.dropna(subset=["p_yes"]).copy()
    bins = np.linspace(0, 1, 11)
    d["b"] = pd.cut(d.p_yes, bins, include_lowest=True)
    rel = d.groupby("b", observed=True).agg(
        n=("yes", "size"), pred=("p_yes", "mean"), real=("yes", "mean")).reset_index()
    # causal-ish monotone recalibration: use the full-sample bin means as the map
    # (a deployable version refits causally; here it documents the calibration gap).
    centers = np.array([iv.mid for iv in rel.b])
    real = rel.real.values
    real = np.maximum.accumulate(real)  # enforce monotone non-decreasing

    def recal(p_yes):
        return np.interp(np.clip(p_yes, 0, 1), centers, real)
    return rel, recal


# =============================================================================
# 3. Sizing rules -> per-window stake (dollars), given bankroll B
# =============================================================================
def stake_flat(row, B):
    # current behavior: CAP_FRAC of print, capped at fixed $ notional, in CONTRACTS*price
    notional = min(row.avail_qty * CAP_FRAC * row.p, PER_WIN_FLAT)
    return notional


def kelly_frac(p_win, p):
    """Kelly fraction of bankroll for buy-at-p, win->(1-p)/lose->-p bet."""
    b = (1.0 - p) / p
    q = 1.0 - p_win
    f = (p_win * b - q) / b           # = p_win - q*p/(1-p)
    return np.clip(f, 0.0, 1.0)


def stake_kelly(row, B, mult):
    f = kelly_frac(row.p_win, row.p) * mult
    notional = f * B
    # liquidity cap: cannot lift more than CAP_FRAC of the print
    notional = min(notional, row.avail_qty * CAP_FRAC * row.p)
    return notional


def stake_cvar(row, B):
    """Size so the worst-case loss on this window <= CVAR_BUDGET*B.
    Worst case = lose -> lose (p + fee) per contract. notional_loss = qty*p; loss_$=qty*(p+fee).
    But only enter if there is positive edge (Kelly>0)."""
    if kelly_frac(row.p_win, row.p) <= 0:
        return 0.0
    loss_per_contract = row.p + row.fee_pc
    max_qty = (CVAR_BUDGET * B) / max(loss_per_contract, 1e-9)
    notional = max_qty * row.p
    notional = min(notional, row.avail_qty * CAP_FRAC * row.p)
    return notional


def stake_kelly_cvar(row, B, mult=0.25):
    """RECOMMENDED: fractional Kelly stake, then HARD-CAPPED so the worst-case loss
    on this window <= CVAR_BUDGET*B. Kelly picks the edge-proportional size; the CVaR
    clamp guarantees no single flip can blow a hole > CVAR_BUDGET of bankroll. This is
    the rule that both grows and survives."""
    f = kelly_frac(row.p_win, row.p) * mult
    if f <= 0:
        return 0.0
    notional = f * B
    # CVaR clamp: loss_per_contract = p+fee; cap qty so qty*(p+fee) <= budget*B
    loss_per_contract = row.p + row.fee_pc
    max_notional_cvar = (CVAR_BUDGET * B) / max(loss_per_contract, 1e-9) * row.p
    notional = min(notional, max_notional_cvar, row.avail_qty * CAP_FRAC * row.p)
    return notional


def stake_edgeprop(row, B):
    """Size proportional to expected net c/contract; zero below a floor."""
    edge_c = (row.p_win * (1 - row.p) - (1 - row.p_win) * row.p - row.fee_pc) * 100.0
    if edge_c < EDGE_FLOOR_C:
        return 0.0
    notional = EDGE_SCALE * edge_c          # $ per cent of edge
    notional = min(notional, row.avail_qty * CAP_FRAC * row.p, MAX_WIN_FRAC * B)
    return notional


# =============================================================================
# 4. Walk the bankroll forward (compounding) under a sizing rule
# =============================================================================
def simulate(f, rule, p_win_col, B0=BANKROLL0):
    """Return per-window returns r_i, pnl_i, stakes, ending bankroll path.
    f must be chrono-sorted. p_win_col selects which prob feeds the rule."""
    f = f.copy()
    f["p_win"] = f[p_win_col]
    B = B0
    rs, pnls, stakes, banks = [], [], [], []
    for _, row in f.iterrows():
        # cash + hard caps
        if rule == "flat":
            notional = stake_flat(row, B)
        elif rule == "kelly":
            notional = stake_kelly(row, B, 1.0)
        elif rule == "kelly_half":
            notional = stake_kelly(row, B, 0.5)
        elif rule == "kelly_qtr":
            notional = stake_kelly(row, B, 0.25)
        elif rule == "cvar":
            notional = stake_cvar(row, B)
        elif rule == "kelly_cvar":
            notional = stake_kelly_cvar(row, B, 0.25)
        elif rule == "edgeprop":
            notional = stake_edgeprop(row, B)
        else:
            raise ValueError(rule)
        # global caps: never stake more than MAX_WIN_FRAC*B or more cash than we have
        notional = min(notional, MAX_WIN_FRAC * B, 0.95 * B)
        qty = notional / row.p if row.p > 0 else 0.0
        # realized pnl: per-contract (1-p)-fee if won else -p-fee
        pnl_pc = ((1 - row.p) - row.fee_pc) if row.won else (-row.p - row.fee_pc)
        pnl = qty * pnl_pc
        r = pnl / B if B > 0 else 0.0
        B = B + pnl
        rs.append(r); pnls.append(pnl); stakes.append(notional); banks.append(B)
        if B <= 0:
            # ruin: pad the rest with zeros
            n_left = len(f) - len(rs)
            rs += [0.0] * n_left; pnls += [0.0] * n_left
            stakes += [0.0] * n_left; banks += [B] * n_left
            break
    out = f.iloc[:len(rs)].copy()
    out["r"] = rs; out["pnl"] = pnls; out["stake"] = stakes; out["bank"] = banks
    return out


def metrics(out, B0=BANKROLL0):
    r = out.r.values
    pnl = out.pnl.values
    Bend = out.bank.values[-1]
    tot_ret = Bend / B0 - 1.0
    # geometric growth rate per window
    glog = np.mean(np.log1p(np.clip(r, -0.999999, None)))
    sharpe = (np.mean(r) / np.std(r) * np.sqrt(len(r))) if np.std(r) > 0 else float("nan")
    # max drawdown on the bankroll path
    bank = out.bank.values
    peak = np.maximum.accumulate(bank)
    dd = (bank - peak) / peak
    maxdd = dd.min()
    # 5% CVaR on per-window pnl ($) and worst window
    n = len(pnl); k = max(1, int(np.ceil(0.05 * n)))
    worst = np.sort(pnl)[:k]
    cvar5 = worst.mean()
    worst_win = pnl.min()
    # skew of per-window pnl
    pp = pnl - pnl.mean()
    sd = pnl.std()
    skew = (np.mean(pp ** 3) / sd ** 3) if sd > 0 else float("nan")
    return dict(
        tot_ret=tot_ret, Bend=Bend, glog=glog, sharpe=sharpe, maxdd=maxdd,
        cvar5=cvar5, worst_win=worst_win, skew=skew,
        n=n, total_pnl=pnl.sum(), avg_stake=out.stake.mean(),
        nbets=(out.stake > 0).sum(),
    )


# =============================================================================
# 5. The known live losing fill: @0.28, thin margin (margin +30 USD -> high q)
# =============================================================================
def size_the_loser(recal, sig):
    """The live -$5.357 loss: bought @0.28 on a +30 (thin) margin print.
    Show each rule's stake on a synthetic row matching it."""
    # thin margin: margin_hat ~ +30 USD at tau=45.  sd_S ~ 14.5 (from model).
    sd = 14.5
    margin_hat = 30.0
    p_yes_raw = norm.cdf(margin_hat / sd)         # model's confidence
    # we bought @0.28 -> we were the YES side priced cheap; p_win(model)=p_yes
    # avail_qty large so the demo reveals each rule's INTRINSIC appetite (not the
    # liquidity cap) — the whole point is what the rule WANTS to stake.
    row = pd.Series(dict(p=0.28, avail_qty=3000.0, p_win=p_yes_raw,
                         p_win_cal=float(recal(p_yes_raw)),
                         fee_pc=float(order_fee_vec(1.0, 0.28, MAKER)),
                         won=False, margin_hat=margin_hat))
    B = BANKROLL0
    res = {}
    for name, fn in [("flat", lambda: min(row.avail_qty*CAP_FRAC*row.p, PER_WIN_FLAT)),
                     ("kelly", lambda: stake_kelly(row, B, 1.0)),
                     ("kelly_half", lambda: stake_kelly(row, B, 0.5)),
                     ("kelly_qtr", lambda: stake_kelly(row, B, 0.25)),
                     ("cvar", lambda: stake_cvar(row, B)),
                     ("edgeprop", lambda: stake_edgeprop(row, B))]:
        # kelly/cvar/edgeprop use row.p_win -> set it to CALIBRATED for honesty
        res[name] = fn()
    return row, res


# =============================================================================
# main
# =============================================================================
def run_gate(thr, recal, label):
    """Full sizing comparison on the gated set at margin gate `thr`."""
    f, _ = build_fills(rate=MAKER, thr=thr)
    print("\n" + "#" * 78)
    print(f"# GATE {label}: |margin_hat| >= {thr:.0f} USD  ->  {len(f)} windows, "
          f"realized win {f.won.mean():.3f}, losers {(~f.won).sum()}")
    print("#" * 78)
    # calibrated model win-prob for the side we bet
    f["p_win_cal"] = np.where(f.bet_yes, recal(f.p_yes), 1 - recal(f.p_yes))
    # BLENDED win-prob: combine calibrated model with market-implied (the price).
    # This is the load-bearing fix: a cheap, confident-model print (adverse selection)
    # gets pulled toward the market's low prob -> high q -> tiny Kelly.
    f["p_win_blend"] = (W_MODEL * f.p_win_cal + (1 - W_MODEL) * f.p_win_mkt).clip(0, 1)
    raw_conf = f.p_win_raw.mean(); real_win = f.won.mean()
    print(f"  mean p_win  raw={raw_conf:.4f}  calibrated={f.p_win_cal.mean():.4f}  "
          f"market(price)={f.p_win_mkt.mean():.4f}  blended={f.p_win_blend.mean():.4f}")
    # calibration on the gated set: does blended track realized better than raw?
    for col in ["p_win_raw", "p_win_cal", "p_win_blend"]:
        gap = f[col].mean() - real_win
        bs = ((f[col] - f.won.astype(float)) ** 2).mean()  # Brier
        print(f"  {col:>12}: mean-gap {gap:+.4f}  Brier {bs:.4f}")

    # which p_win the Kelly family uses
    pcol = "p_win_blend" if KELLY_CAL else "p_win_raw"

    # ---- simulate every rule ----
    rules = [("flat", "p_win_raw"), ("kelly", pcol), ("kelly_half", pcol),
             ("kelly_qtr", pcol), ("cvar", pcol), ("kelly_cvar", pcol),
             ("edgeprop", pcol)]
    mid = f.close_dt.quantile(0.5)
    print("\n--- SIZING RULES on the SAME gated fills (MAKER fees, B0=$%.0f, compounding) ---" % BANKROLL0)
    hdr = (f"{'rule':>11} | {'tot_ret':>9} {'Bend$':>9} {'E[log]/w':>9} "
           f"{'Sharpe':>7} {'maxDD':>7} {'CVaR5$':>8} {'worst$':>8} {'skew':>7} "
           f"{'avgStk$':>8} {'nbets':>6}")
    print(hdr); print("-" * len(hdr))
    sims = {}
    for name, col in rules:
        out = simulate(f, name, col)
        sims[name] = out
        mtr = metrics(out)
        print(f"{name:>11} | {mtr['tot_ret']*100:>8.1f}% {mtr['Bend']:>9.1f} "
              f"{mtr['glog']*1e4:>8.2f}b {mtr['sharpe']:>7.2f} {mtr['maxdd']*100:>6.1f}% "
              f"{mtr['cvar5']:>8.3f} {mtr['worst_win']:>8.2f} {mtr['skew']:>7.2f} "
              f"{mtr['avg_stake']:>8.2f} {mtr['nbets']:>6d}")

    # ---- IS/OOS split ----
    print("\n--- IN-SAMPLE (H1) vs OUT-OF-SAMPLE (H2), split at median close_dt ---")
    print(f"{'rule':>11} | {'IS E[log]/w':>12} {'IS CVaR5$':>10} | {'OOS E[log]/w':>13} {'OOS CVaR5$':>11}")
    for name, col in rules:
        out = sims[name]
        h1 = metrics(out[out.close_dt < mid]) if (out.close_dt < mid).sum() else None
        h2 = metrics(out[out.close_dt >= mid]) if (out.close_dt >= mid).sum() else None
        s1 = f"{h1['glog']*1e4:>11.2f}b {h1['cvar5']:>10.3f}" if h1 else " " * 22
        s2 = f"{h2['glog']*1e4:>12.2f}b {h2['cvar5']:>11.3f}" if h2 else ""
        print(f"{name:>11} | {s1} | {s2}")

    # ---- PnL distribution skew shift: percentiles ----
    print("\n--- PnL DISTRIBUTION per window ($): flat (left-skewed) -> kelly_half (right) ---")
    print(f"{'rule':>11} | {'p1':>8} {'p5':>8} {'p25':>8} {'p50':>8} {'p75':>8} {'p95':>8} {'p99':>8} {'skew':>7}")
    for name in ["flat", "kelly", "kelly_half", "kelly_qtr", "cvar", "kelly_cvar", "edgeprop"]:
        p = sims[name].pnl.values
        qs = np.percentile(p, [1, 5, 25, 50, 75, 95, 99])
        sk = metrics(sims[name])["skew"]
        print(f"{name:>11} | " + " ".join(f"{q:>8.3f}" for q in qs) + f" {sk:>7.2f}")

    # ---- worst-case framing: max single-window loss by rule ----
    print("\n--- realized WORST single-window loss by rule (the 'one flip wipes it' metric) ---")
    for name in ["flat", "kelly", "kelly_half", "kelly_qtr", "cvar", "kelly_cvar", "edgeprop"]:
        print(f"    {name:>11}: worst window ${sims[name].pnl.min():>8.2f}  "
              f"(= {sims[name].pnl.min()/BANKROLL0*100:>6.2f}% of B0)")
    return f, sims


def main():
    print("=" * 78)
    print("A3 SIZING — bankroll geometry for the BTC panic-fade")
    print(f"  TAU={TAU}s  CAP={CAP}  CAP_FRAC={CAP_FRAC}  B0=${BANKROLL0:.0f}  "
          f"CVaR_budget={CVAR_BUDGET:.0%}  W_MODEL(q-blend)={W_MODEL}")
    print("=" * 78)
    sig = F.estimate_sigma_sec(L.binance_matrix())

    # ---- calibration (computed once, on the FULL universe) ----
    _, mp = build_fills(rate=MAKER, thr=0.0)
    rel, recal = calibrate(mp)
    print("\n--- CALIBRATION of model p_yes (reliability, full universe) ---")
    print("  model is well-calibrated at the EXTREMES but OVERCONFIDENT in the middle:")
    print(rel.to_string(index=False,
          formatters={"pred": "{:.3f}".format, "real": "{:.3f}".format}))

    # ---- two regimes ----
    # THR=10: the live regime — has real adverse-selection losers (the user's pain).
    # THR=20: a profitable gate — where good sizing actually shifts the dist RIGHT.
    run_gate(10.0, recal, "LIVE REGIME (loss-laden)")
    run_gate(20.0, recal, "PROFITABLE GATE")

    # ---- how each rule sizes the KNOWN live losing fill (@0.28, thin margin) ----
    print("\n" + "=" * 78)
    print("HOW EACH RULE SIZES THE LIVE LOSING FILL (@0.28, margin +30 USD, thin)")
    print("=" * 78)
    row, _ = size_the_loser(recal, sig)
    p_win_blend = float(np.clip(W_MODEL * row.p_win_cal + (1 - W_MODEL) * row.p, 0, 1))
    print(f"  inputs: price=0.28  margin_hat=+30 USD  model p_yes(raw)={row.p_win:.3f}")
    print(f"  p_win:  calibrated={row.p_win_cal:.3f}  market(price)=0.280  BLENDED={p_win_blend:.3f}")
    print(f"  -> MODEL ALONE: Kelly f*={kelly_frac(row.p_win_cal,0.28):.3f} (would bet big);")
    print(f"     BLENDED (recommended): f*={kelly_frac(p_win_blend,0.28):.4f} — adverse-selection guard.")
    rc = row.copy(); rc["p_win"] = p_win_blend
    res2 = {
        "flat": min(row.avail_qty*CAP_FRAC*row.p, PER_WIN_FLAT),
        "kelly": stake_kelly(rc, BANKROLL0, 1.0),
        "kelly_half": stake_kelly(rc, BANKROLL0, 0.5),
        "kelly_qtr": stake_kelly(rc, BANKROLL0, 0.25),
        "cvar": stake_cvar(rc, BANKROLL0),
        "kelly_cvar": stake_kelly_cvar(rc, BANKROLL0, 0.25),
        "edgeprop": stake_edgeprop(rc, BANKROLL0),
    }
    for k, v in res2.items():
        qty = v / row.p
        print(f"    {k:>11}: stake ${v:>7.2f}  ({qty:>6.1f} contracts)  "
              f"-> if it loses: -${qty*(row.p+row.fee_pc):>6.2f}")

    # ---- fee sensitivity on the recommended rule + gate ----
    print("\n" + "=" * 78)
    print("Fee sensitivity (kelly_cvar @ THR=20): ZERO / MAKER / TAKER")
    for rate, lab in [(ZERO, "ZERO"), (MAKER, "MAKER"), (TAKER, "TAKER")]:
        ff, _ = build_fills(rate=rate, thr=20.0)
        ff["p_win_cal"] = np.where(ff.bet_yes, recal(ff.p_yes), 1 - recal(ff.p_yes))
        ff["p_win_blend"] = (W_MODEL * ff.p_win_cal + (1 - W_MODEL) * ff.p).clip(0, 1)
        o = simulate(ff, "kelly_cvar", "p_win_blend")
        mt = metrics(o)
        print(f"    {lab:>6}: tot_ret {mt['tot_ret']*100:>7.1f}%  E[log]/w {mt['glog']*1e4:>6.2f}bp  "
              f"worst ${mt['worst_win']:>7.2f}")
    print("=" * 78)


if __name__ == "__main__":
    main()

"""A6 — strategies mined from Kakushadze & Serur, "151 Trading Strategies" (2018),
translated to our single-underlying, ultra-short-dated binary, MAKER setting.

Which book strategies are PORTABLE (and which are not):
  * Ch 3.8 Pairs Trading / 3.9-3.10 Mean-Reversion ("rich/cheap" via demeaned
    return z-score): the cross-sectional core does NOT port (we have one asset),
    BUT the *univariate* primitive does: treat (model fair value p_yes) vs
    (Kalshi market price) as the "pair". The market price is the noisy leg that
    mean-reverts toward fair value. Buy the side the market prices CHEAP relative
    to fair by > k * sd, in z-score units. This GENERALIZES the panic-fade (which
    only ever bought one model-chosen side at any cheap print) to a signed,
    z-scored fair-value statistical arbitrage. -> TESTED (S1).
  * Ch 3.19 Market-Making under adverse selection: the book's exact diagnosis of
    our -$5 loss. "In a market where most order flow is smart/toxic, [capturing
    the spread] loses money ... most fills at the bid would be when the market is
    trading through it downward." Fix: modulate the passive bid with a
    LONGER-HORIZON signal (our model edge) and only rest where the long signal
    confirms; skip toxic prints. -> TESTED as the edge-gate inside S1/S2.
  * Ch 7.4 Volatility Risk Premium + Ch 7 vol-based sizing: sell "expensive"
    insurance / scale exposure by volatility. Port: gate & size by our model
    uncertainty sd_S (trade more when edge >> uncertainty, less when sd_S high).
    -> TESTED (S2, the vol-scaled edge gate + sd-inverse sizing).
  * Ch 2 Options (covered call, spreads, straddles, condors, ...): multi-leg
    payoff engineering on a continuous-payoff option. Not portable to a single
    all-or-nothing binary with no option chain to spread against. The only
    transferable nugget is the binary-option valuation identity P(YES)=P(S>=K),
    which we already compute as p_yes. -> NOT APPLICABLE (noted, moved on).
  * Ch 18 Cryptocurrencies (ANN technical-indicator forecaster, Twitter naive
    Bayes): directional price forecasters on raw BTC, not edge vs a binary's
    fair value, and look-ahead/over-fit prone. Our causal TWAP model already IS
    the forecaster, and is far higher-confidence near lock. -> NOT APPLICABLE.
  * Everything cross-sectional (multifactor, residual momentum, single/multi
    cluster MR, alpha combos) and every other asset class (ETF/FX/convertible/
    distressed/tax/real-estate/...) needs a universe of names -> NOT APPLICABLE.

S1 = Fair-Value Statistical Arbitrage  (Pairs/Mean-Reversion, ch 3.8-3.10)
S2 = Vol-Premium-Gated Fair-Value Arb  (S1 + ch 7.4 vol gate + sd-inverse size)
Both are compared head-to-head against the current panic-fade baseline, all
under the corrected MAKER fee model (backtest/strategy_search/fees.py).

Run:  python -m backtest.strategy_search.a6_book_strategies
"""
from __future__ import annotations
import numpy as np, pandas as pd
from backtest.btc_lib import load_markets, load_trades, binance_matrix, raw_avg60, causal_bias, estimate
from backtest.analysis.fade_lib import model_pwin, estimate_sigma_sec
from backtest.strategy_search import fees

TAU = 45                 # decision second-to-close (matches the live operating point)
SEC_LO, SEC_HI = 5, 45   # actionable print window
SAMPLE_FRAC = 2500 / 6308
# Per-window NOTIONAL cap in dollars (the live operating constraint: $5/window).
# Tail metrics are only meaningful under a realistic per-window cap, else the
# worst window is just "the window with the most printed volume".
PER_WIN_NOTIONAL = 5.0
CAP_FRAC = 1.0           # share of printed cheap volume we assume we capture

# ----------------------------------------------------------------------------
# Build the causal model frame once: p_yes, sd_S, margin_hat per ticker.
# ----------------------------------------------------------------------------
def build_frame():
    m = load_markets()
    piv = binance_matrix()
    m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
    sigma_sec = estimate_sigma_sec(piv)
    mp = model_pwin(piv.loc[m.ticker], m, TAU, sigma_sec)   # indexed by ticker
    mp = mp.reset_index()
    # sd of p_yes induced by sd of the margin estimate (delta-method through the
    # normal cdf): sd_p ~= phi(margin_hat/sd_S) * (sd_margin/sd_S). We use sd_S as
    # the margin uncertainty; this is the natural z-score denominator for the MR.
    from scipy.stats import norm
    z = (mp["margin_hat"] / mp["sd_S"].clip(lower=1e-6)).values
    sd_p = norm.pdf(z)            # = phi(z) * (sd_S/sd_S); the prob-space std unit
    mp["sd_p"] = np.clip(sd_p, 1e-3, None)
    return m, mp, sigma_sec


def trades_at_window(tr):
    """All prints in the actionable window, with both side prices."""
    d = tr[(tr.sec_to_close >= SEC_LO) & (tr.sec_to_close <= SEC_HI)].copy()
    return d


# ----------------------------------------------------------------------------
# PnL accounting under a given fee rate. One bet side per print; hold to settle.
# ----------------------------------------------------------------------------
def _apply_window_cap(d):
    """Cap cumulative notional (qty*price) per ticker-window to PER_WIN_NOTIONAL,
    in print order (earliest actionable second first). Mirrors the live $5/window
    constraint so tail metrics are comparable across strategies."""
    d = d.sort_values(["ticker", "sec_to_close"], ascending=[True, False]).copy()
    d["notional"] = d.qty * d.win_px
    cum = d.groupby("ticker")["notional"].cumsum()
    prev = cum - d["notional"]
    room = (PER_WIN_NOTIONAL - prev).clip(lower=0.0)
    keep_qty = np.minimum(d.qty, room / d.win_px.clip(lower=1e-6))
    d["qty"] = keep_qty
    d = d[d.qty > 1e-9]
    return d


def _settle(d, rate):
    """d must have: bet_yes, yes(outcome), win_px (price paid on bet side), qty.
    Returns d with pnl (dollars) using one-round-up MAKER/TAKER/ZERO order fee."""
    d = _apply_window_cap(d)
    d["won"] = np.where(d.bet_yes, d.yes == 1, d.yes == 0)
    gross_ct = np.where(d.won, 1.0 - d.win_px, -d.win_px)        # per contract, dollars
    fee_ct = fees.fee_per_contract(d.qty.values, d.win_px.values, rate)
    d["pnl_ct"] = gross_ct - fee_ct
    d["pnl"] = d.pnl_ct * d.qty
    return d


def summarize(d, label, n_total_windows, days):
    if len(d) == 0:
        return None
    ct = d.qty.sum()
    pnl = d.pnl.sum()
    wr = (d.won * d.qty).sum() / ct
    pw = d.groupby("ticker").pnl.sum()           # per-window dollar PnL
    nwin = pw.shape[0]
    nlose = int((d.groupby("ticker").won.mean() < 0.5).sum())
    worst = pw.min()
    # 5% CVaR on the per-window PnL distribution (mean of worst 5%)
    q05 = np.percentile(pw.values, 5)
    cvar5 = pw[pw <= q05].mean() if (pw <= q05).any() else pw.min()
    usd_day = (pnl / SAMPLE_FRAC) / days         # scale sample -> full universe / day
    return dict(label=label, net_c=pnl / ct * 100, wr=wr * 100, contracts=int(ct),
                windows=nwin, losers=nlose, worst=worst, cvar5=cvar5,
                usd_day=usd_day, pnl=pnl)


# ============================================================================
# BASELINE: current panic-fade (model picks a side via margin_hat; lift any cheap
# print on that side <= CAP). Reimplemented here under corrected MAKER fees.
# ============================================================================
def run_fade(m, mp, tr, THR_MARGIN=10.0, CAP=0.99, rate=fees.MAKER):
    g = mp[mp.margin_hat.abs() >= THR_MARGIN].copy()
    g["bet_yes"] = g.margin_hat > 0
    d = tr.merge(g[["ticker", "bet_yes"]], on="ticker", how="inner")
    d = d.merge(m[["ticker", "yes", "close_dt"]], on="ticker", how="inner")
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px > 0) & (d.win_px <= CAP)]
    d["qty"] = d["size"] * CAP_FRAC
    return _settle(d, rate)


# ============================================================================
# S1: FAIR-VALUE STATISTICAL ARBITRAGE  (ch 3.8-3.10 pairs / mean-reversion)
#   Define the model fair price on a side:  fair_yes = p_yes, fair_no = 1-p_yes.
#   The market is "cheap" on a side when its ask price < fair by > K_SD * sd_p.
#   z = (fair_side - market_side_px) / sd_p  > K_SD   ->  the side is mispriced
#   cheap; BUY it (we are the resting maker that the panic seller hits).
#   This is the demeaned-return "rich/cheap" rule with the model fair value as
#   the mean and sd_p as the dispersion. It is SIGNED & SYMMETRIC: it will buy
#   YES when YES is cheap OR NO when NO is cheap, unlike the fade which always
#   buys the single model-favored side regardless of how cheap it is.
#   ADVERSE-SELECTION GATE (ch 3.19): also require a minimum model edge
#   (|margin_hat| >= EDGE_MIN) so we never rest on a side the long-horizon
#   signal does not confirm -> avoids the cheap+thin-margin toxic prints.
# ============================================================================
def run_fvarb(m, mp, tr, K_SD=1.5, CAP=0.99, EDGE_MIN=0.0, rate=fees.MAKER):
    f = mp[["ticker", "p_yes", "sd_p", "margin_hat"]].copy()
    d = tr.merge(f, on="ticker", how="inner")
    d = d.merge(m[["ticker", "yes", "close_dt"]], on="ticker", how="inner")
    # z-score of cheapness on each side (fair - ask)/sd_p
    z_yes = (d.p_yes - d.yes_price) / d.sd_p
    z_no = ((1.0 - d.p_yes) - d.no_price) / d.sd_p
    buy_yes = (z_yes >= K_SD) & (z_yes >= z_no)
    buy_no = (z_no >= K_SD) & (z_no > z_yes)
    take = buy_yes | buy_no
    # adverse-selection gate: model edge must confirm the side we are buying
    edge_ok = (np.where(buy_yes, d.margin_hat, -d.margin_hat) >= EDGE_MIN)
    take = take & edge_ok
    d = d[take].copy()
    # side we are buying = whichever leg is more cheap (recomputed on the filtered frame)
    zy = (d.p_yes - d.yes_price) / d.sd_p
    zn = ((1.0 - d.p_yes) - d.no_price) / d.sd_p
    d["bet_yes"] = (zy >= zn)
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px > 0) & (d.win_px <= CAP)]
    d["qty"] = d["size"] * CAP_FRAC
    return _settle(d, rate)


# ============================================================================
# S2: VOL-PREMIUM-GATED FAIR-VALUE ARB  (S1 + ch 7.4 vol risk premium / sizing)
#   The vol-risk-premium idea: only "sell insurance" (take the bet) when the
#   premium (our edge in prob units) exceeds the uncertainty by a margin, and
#   size INVERSELY to uncertainty sd_p (trade big when edge dwarfs noise, small
#   when noisy). Concretely: edge_p = fair_side - market_side_px ; require
#   edge_p >= K_SD * sd_p  AND  size weight w = clip(edge_p / sd_p, 0, WMAX).
# ============================================================================
def run_fvarb_asym(m, mp, tr, K_SD=1.5, CAP=0.99, rate=fees.MAKER):
    """ASYMMETRIC S1: only ever buy the MODEL-FAVORED side (margin_hat sign), and
    only when that side is also priced cheap by > K_SD*sd_p. This is the fade with
    the book's z-score cheapness gate bolted on, and it NEVER fades a rich winner
    (the move that caused the OOS losses in the symmetric version). Tests whether
    the z-score gate adds value over the plain fade without the toxic symmetric leg."""
    f = mp[["ticker", "p_yes", "sd_p", "margin_hat"]].copy()
    f = f[f.margin_hat.abs() > 0]
    f["bet_yes"] = f.margin_hat > 0
    d = tr.merge(f, on="ticker", how="inner")
    d = d.merge(m[["ticker", "yes", "close_dt"]], on="ticker", how="inner")
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    fair_side = np.where(d.bet_yes, d.p_yes, 1.0 - d.p_yes)
    z = (fair_side - d.win_px) / d.sd_p
    d = d[(z >= K_SD) & (d.win_px > 0) & (d.win_px <= CAP)].copy()
    d["qty"] = d["size"] * CAP_FRAC
    return _settle(d, rate)


def run_volarb(m, mp, tr, K_SD=1.5, CAP=0.99, WMAX=4.0, rate=fees.MAKER):
    f = mp[["ticker", "p_yes", "sd_p", "margin_hat"]].copy()
    d = tr.merge(f, on="ticker", how="inner")
    d = d.merge(m[["ticker", "yes", "close_dt"]], on="ticker", how="inner")
    zy = (d.p_yes - d.yes_price) / d.sd_p
    zn = ((1.0 - d.p_yes) - d.no_price) / d.sd_p
    d["bet_yes"] = (zy >= zn)
    d["z"] = np.where(d.bet_yes, zy, zn)
    d = d[d.z >= K_SD].copy()
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px > 0) & (d.win_px <= CAP)]
    # vol-scaled sizing: weight ~ z (edge in sd units), capped
    w = np.clip(d["z"].values, 0, WMAX) / WMAX
    d["qty"] = d["size"] * CAP_FRAC * w
    return _settle(d, rate)


def boot_ci(pw, n=2000, seed=0):
    pw = np.asarray(pw)
    if len(pw) < 5:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = np.array([rng.choice(pw, len(pw), replace=True).sum() / len(pw) for _ in range(n)])
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def report_row(s):
    if s is None:
        return f"{'(no trades)':>40}"
    return (f"{s['label']:<26} net {s['net_c']:>6.2f}c  wr {s['wr']:>5.1f}%  "
            f"win/los {s['windows']:>4}/{s['losers']:<3}  k_ct {s['contracts']/1000:>6.1f}  "
            f"$/day {s['usd_day']:>7.1f}  worst {s['worst']:>7.2f}  CVaR5 {s['cvar5']:>7.2f}")


def main():
    m, mp, sigma_sec = build_frame()
    tr = trades_at_window(load_trades())
    tr = tr[tr.ticker.isin(m.ticker)]
    days = (m.close_dt.max() - m.close_dt.min()).days
    mid = m.close_dt.quantile(0.5)
    is_tk = set(m[m.close_dt < mid].ticker)
    oos_tk = set(m[m.close_dt >= mid].ticker)
    print(f"period {days} days; sigma_sec={sigma_sec:.2f}; "
          f"sample coverage {SAMPLE_FRAC:.2f}; tau={TAU}\n")

    def evalrun(fn, label, **kw):
        d = fn(m, mp, tr, **kw)
        full = summarize(d, label, len(m), days)
        di = d[d.ticker.isin(is_tk)]; do = d[d.ticker.isin(oos_tk)]
        h1 = summarize(di, label + " IS", len(is_tk), days / 2)
        h2 = summarize(do, label + " OOS", len(oos_tk), days / 2)
        return full, h1, h2, d

    print("FULL SAMPLE (all under corrected MAKER fees unless noted)")
    print("-" * 118)

    base_full, base_h1, base_h2, base_d = evalrun(run_fade, "BASELINE fade THR10", THR_MARGIN=10.0)
    print(report_row(base_full))

    rows = []
    for K in [1.0, 1.5, 2.0, 2.5]:
        full, h1, h2, _ = evalrun(run_fvarb, f"S1 fv-arb K={K}", K_SD=K)
        rows.append((full, h1, h2))
        print(report_row(full))
    # S1 + adverse-selection edge gate (ch 3.19): require model margin confirm
    for K, E in [(1.5, 5.0), (1.5, 10.0), (2.0, 10.0)]:
        full, h1, h2, _ = evalrun(run_fvarb, f"S1+ASgate K={K} E={E:.0f}", K_SD=K, EDGE_MIN=E)
        print(report_row(full))
    # S1-ASYM: only buy the model-favored side AND require z-score cheapness
    for K in [1.0, 1.5, 2.0]:
        full, h1, h2, _ = evalrun(run_fvarb_asym, f"S1-asym K={K}", K_SD=K)
        print(report_row(full))
    for K in [1.0, 1.5, 2.0]:
        full, h1, h2, _ = evalrun(run_volarb, f"S2 vol-arb K={K}", K_SD=K)
        print(report_row(full))

    print("\nIN-SAMPLE vs OUT-OF-SAMPLE (split at median close_dt)")
    print("-" * 118)
    for fn, label, kw in [
        (run_fade, "BASELINE fade THR10", dict(THR_MARGIN=10.0)),
        (run_fvarb, "S1 fv-arb K=1.5", dict(K_SD=1.5)),
        (run_fvarb, "S1 fv-arb K=2.0", dict(K_SD=2.0)),
        (run_fvarb, "S1+ASgate K=1.5 E=10", dict(K_SD=1.5, EDGE_MIN=10.0)),
        (run_fvarb, "S1+ASgate K=2.0 E=10", dict(K_SD=2.0, EDGE_MIN=10.0)),
        (run_volarb, "S2 vol-arb K=1.5", dict(K_SD=1.5)),
    ]:
        full, h1, h2, d = evalrun(fn, label, **kw)
        print(report_row(h1))
        print(report_row(h2))
        lo, hi = boot_ci(d.groupby("ticker").pnl.sum().values)
        print(f"{'':<4}full per-window PnL 95% CI: ({lo:+.3f}, {hi:+.3f}) $/window; "
              f"net {full['net_c']:.2f}c, {full['windows']} windows\n")

    # Sensitivity to fee regime on the headline candidate
    print("FEE REGIME on S1 K=1.5 (ZERO optimistic / MAKER headline / TAKER pessimistic)")
    print("-" * 118)
    for rate, nm in [(fees.ZERO, "ZERO"), (fees.MAKER, "MAKER"), (fees.TAKER, "TAKER")]:
        d = run_fvarb(m, mp, tr, K_SD=1.5, rate=rate)
        s = summarize(d, f"S1 K=1.5 {nm}", len(m), days)
        print(report_row(s))


if __name__ == "__main__":
    main()

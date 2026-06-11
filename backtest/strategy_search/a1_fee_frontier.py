"""Agent A1 — corrected-fee economics + the operating-point frontier.

Re-derives the TWAP panic-fade edge under the CORRECTED Kalshi fee model
(`backtest/strategy_search/fees.py`): we REST cheap bids and let panic sellers
hit us, so WE ARE THE MAKER (rate 0.0175, ONE round-up per ORDER), not the taker
(0.07) with a per-CONTRACT 1c floor the old code charged (~16x too high + wrong
role + wrong rounding).

Everything here is CAUSAL — reuses btc_lib's causal de-bias (window i uses only
windows < i). The decision rule mirrors final_strategy.run:
  decision at tau=45s, gate |margin_hat| >= THR (USD), target model-winning side,
  lift any print on the winning side at price <= CAP over sec_to_close in [SEC_LO,45],
  hold to settlement. PnL/ct: win -> (1-p) - fee/ct ; lose -> -p - fee/ct.

KEY FEE DIFFERENCE vs final_strategy:
  Maker fee is per-ORDER (one round-up across the whole captured size in a window),
  so we amortize the order fee across the window's contracts at that price rather
  than charging a 1c floor on every contract. We group a window's captured volume
  by fill price and compute one round-up per (window, price) order.

Outputs: full CAP x THR grid with net c/ct, win%, #windows, #losers, contracts,
est $/day, worst-window PnL, 5% CVaR, and H1(in-sample) vs H2(OOS) split — under
MAKER (headline), ZERO (optimistic), and old-TAKER (pessimistic) fee models.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from backtest.btc_lib import (load_markets, binance_matrix, raw_avg60,
                              causal_bias, estimate, load_trades)
from backtest.strategy_search import fees

SAMPLE_FRAC = 2500 / 6308    # trades.parquet covers an even 2500-window sample

# ----------------------------------------------------------------------------
# Build the causal decision frame once (tau=45).
# ----------------------------------------------------------------------------
TAU = 45
m = load_markets()
piv = binance_matrix()
m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
raw60 = raw_avg60(piv)
m["delta"] = causal_bias(m, raw60).values
dser = pd.Series(m.delta.values, index=m.ticker.values)
shat = estimate(piv.loc[m.ticker], TAU, dser)
m["mhat"] = shat.values - m.strike.values
m = m.dropna(subset=["delta", "mhat"]).reset_index(drop=True)
tr = load_trades()
MID = m.close_dt.quantile(.5)
DAYS = (m.close_dt.max() - m.close_dt.min()).days

CAPS = [0.99, 0.985, 0.98, 0.97, 0.95, 0.92, 0.90]
THRS = [10, 20, 30, 50, 75]
SEC_LO, SEC_HI = 5, 45

# Fee models to evaluate side by side.  Each maps (qty_per_order, price) -> $ fee.
def maker_order_fee(qty, price):  return fees.order_fee(qty, price, fees.MAKER)
def zero_order_fee(qty, price):   return 0.0
def taker_old_per_ct(qty, price): return float(fees.old_fee_per_contract(price)) * qty   # old 1c-floor, per-contract

FEE_MODELS = {"MAKER": maker_order_fee, "ZERO": zero_order_fee, "OLD_TAKER": taker_old_per_ct}


def captured(THR, CAP):
    """Causal captured-fill frame for a (THR,CAP) cell, BEFORE fees.
    One row per (ticker, win_px) with summed qty -> matches per-ORDER fee rounding
    (a maker order at one price in one window is rounded up once)."""
    g = m[m.mhat.abs() >= THR][["ticker", "mhat", "yes", "close_dt"]].copy()
    g["bet_yes"] = g.mhat > 0
    d = tr.merge(g, on="ticker", how="inner")
    d = d[(d.sec_to_close >= SEC_LO) & (d.sec_to_close <= SEC_HI)]
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px > 0) & (d.win_px <= CAP)]
    if len(d) == 0:
        return d
    d["won"] = np.where(d.bet_yes, d.yes == 1, d.yes == 0)
    # group to one order per (ticker, price) so the maker round-up is applied once
    grp = (d.groupby(["ticker", "win_px"])
             .agg(qty=("size", "sum"), won=("won", "first"),
                  close_dt=("close_dt", "first"))
             .reset_index())
    return grp


def cell_stats(grp, fee_fn):
    """Stats for a captured frame under one fee model. Returns dict or None."""
    if len(grp) == 0:
        return None
    g = grp.copy()
    g["fee"] = [fee_fn(q, p) for q, p in zip(g.qty, g.win_px)]
    g["gross"] = np.where(g.won, 1 - g.win_px, -g.win_px) * g.qty
    g["pnl"] = g["gross"] - g["fee"]
    ct = g.qty.sum()
    pnl = g.pnl.sum()
    wr = (g.won * g.qty).sum() / ct * 100
    # per-window PnL (sum across the window's orders) for tail metrics
    pw = g.groupby("ticker").pnl.sum().values
    pw = np.sort(pw)
    k = max(1, int(np.ceil(0.05 * len(pw))))
    cvar5 = pw[:k].mean()                 # mean of worst 5% windows
    usd_day = pnl / SAMPLE_FRAC / DAYS    # scale sample -> full universe -> per day
    nwin = g.ticker.nunique()
    losers = g[~g.won].ticker.nunique()
    return dict(net_c=pnl / ct * 100, wr=wr, windows=nwin, losers=losers,
                contracts=int(ct), usd_day=usd_day, worst=pw.min(), cvar5=cvar5,
                pnl_total=pnl)


def split_net(grp, fee_fn):
    """(H1 net_c, H2 net_c) in-sample/out-of-sample at median close_dt."""
    out = {}
    for tag, sub in (("H1", grp[grp.close_dt < MID]), ("H2", grp[grp.close_dt >= MID])):
        s = cell_stats(sub, fee_fn)
        out[tag] = s["net_c"] if s else float("nan")
    return out["H1"], out["H2"]


def main():
    print(f"period {DAYS} days; trade-sample coverage {SAMPLE_FRAC:.3f}; "
          f"decision tau={TAU}s, sec[{SEC_LO},{SEC_HI}]; gated windows={len(m)}\n")
    print("net c/ct, win%, #win/#los, k contracts, $/day, worst-window $, 5% CVaR $, H1->H2 net_c\n")

    rows = []
    for CAP in CAPS:
        for THR in THRS:
            grp = captured(THR, CAP)
            rec = {"CAP": CAP, "THR": THR}
            for name, fn in FEE_MODELS.items():
                s = cell_stats(grp, fn)
                rec[name] = s
            if rec["MAKER"]:
                h1, h2 = split_net(grp, maker_order_fee)
                rec["H1"], rec["H2"] = h1, h2
            rows.append(rec)

    # ---- MAKER headline table -------------------------------------------------
    print("=" * 120)
    print("MAKER FEE (0.0175, per-order round-up) — HEADLINE")
    print("=" * 120)
    hdr = (f"{'CAP':>6}{'THR':>5} | {'net_c':>7}{'win%':>8}{'win/los':>9}"
           f"{'k_ct':>8}{'$/day':>8}{'worst$':>9}{'CVaR5$':>9} | {'H1_c':>7}{'H2_c':>7}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        s = r["MAKER"]
        if not s:
            print(f"{r['CAP']:>6}{r['THR']:>5} |  (no fills)")
            continue
        print(f"{r['CAP']:>6}{r['THR']:>5} | {s['net_c']:>7.2f}{s['wr']:>8.2f}"
              f"{str(s['windows'])+'/'+str(s['losers']):>9}{s['contracts']/1000:>8.1f}"
              f"{s['usd_day']:>8.0f}{s['worst']:>9.2f}{s['cvar5']:>9.2f} | "
              f"{r['H1']:>7.2f}{r['H2']:>7.2f}")

    # ---- net_c comparison across fee models ----------------------------------
    print("\n" + "=" * 90)
    print("net c/ct by fee model  (MAKER headline | ZERO optimistic | OLD_TAKER pessimistic)")
    print("=" * 90)
    print(f"{'CAP':>6}{'THR':>5} | {'MAKER':>8}{'ZERO':>8}{'OLD_TAKER':>11} | {'k_ct':>8}{'win%':>7}")
    for r in rows:
        if not r["MAKER"]:
            continue
        mk, z, ot = r["MAKER"], r["ZERO"], r["OLD_TAKER"]
        print(f"{r['CAP']:>6}{r['THR']:>5} | {mk['net_c']:>8.2f}{z['net_c']:>8.2f}"
              f"{ot['net_c']:>11.2f} | {mk['contracts']/1000:>8.1f}{mk['wr']:>7.2f}")

    # ---- frequency gain by cap (THR held at a representative gate) ------------
    print("\n" + "=" * 70)
    print("FREQUENCY by CAP (THR=50, the robust gate): contracts & windows")
    print("=" * 70)
    base = None
    print(f"{'CAP':>6}{'k_ct':>9}{'windows':>9}{'vs_CAP0.99':>12}")
    for r in rows:
        if r["THR"] != 50 or not r["MAKER"]:
            continue
        s = r["MAKER"]
        if base is None:
            base = s["contracts"]
        mult = s["contracts"] / base if base else float("nan")
        print(f"{r['CAP']:>6}{s['contracts']/1000:>9.1f}{s['windows']:>9}{mult:>11.2f}x")

    # ---- frontier: maximize $/day s.t. win%>=99 and bounded left tail --------
    print("\n" + "=" * 90)
    print("FRONTIER — feasible cells (MAKER win% >= 99.0)")
    print("=" * 90)
    feas = [r for r in rows if r["MAKER"] and r["MAKER"]["wr"] >= 99.0]
    feas_by_day = sorted(feas, key=lambda r: -r["MAKER"]["usd_day"])
    feas_by_ct = sorted(feas, key=lambda r: -r["MAKER"]["net_c"])
    print(f"  top by $/day: CAP={feas_by_day[0]['CAP']} THR={feas_by_day[0]['THR']} "
          f"-> ${feas_by_day[0]['MAKER']['usd_day']:.0f}/day, net {feas_by_day[0]['MAKER']['net_c']:.2f}c, "
          f"win {feas_by_day[0]['MAKER']['wr']:.2f}%, CVaR5 ${feas_by_day[0]['MAKER']['cvar5']:.2f}")
    print(f"  top by c/ct : CAP={feas_by_ct[0]['CAP']} THR={feas_by_ct[0]['THR']} "
          f"-> net {feas_by_ct[0]['MAKER']['net_c']:.2f}c, ${feas_by_ct[0]['MAKER']['usd_day']:.0f}/day, "
          f"win {feas_by_ct[0]['MAKER']['wr']:.2f}%, CVaR5 ${feas_by_ct[0]['MAKER']['cvar5']:.2f}")

    return rows


if __name__ == "__main__":
    main()

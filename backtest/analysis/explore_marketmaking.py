"""
explore_marketmaking.py — Pure two-sided market-making / spread-capture backtest
for Kalshi crypto 15-min markets (KXBTC15M, KXETH15M) and hourly ladders.

STRATEGY UNDER TEST
-------------------
Rest two-sided quotes inside/at the touch: a BUY-YES bid at price `b_yes` and a
BUY-NO bid at price `b_no` (= offer YES at 1-b_no). When both sides fill we have
bought 1 YES + 1 NO for (b_yes + b_no) < $1, and the pair settles to exactly
$1.00 -> we capture spread = 1 - (b_yes + b_no), minus maker fees on both legs.
Single-sided (unpaired) fills leave directional inventory that we MARK TO ACTUAL
SETTLEMENT (captures adverse selection: we fill on the side about to lose).

FILL MODEL (the realistic part)
-------------------------------
Kalshi book is BIDS-ONLY; yes_ask = 1 - best_no_bid. The trade tape gives
taker_side + yes_price:
  - taker_side='no'  => taker SOLD yes / bought no, hitting resting YES bids.
                        Our resting YES bid at b fills from this flow iff yes_price <= b.
  - taker_side='yes' => taker BOUGHT yes, lifting resting YES offers (= NO bids).
                        Our resting NO bid at 1-b fills iff yes_price >= 1-b
                        (equivalently no_price <= b).
QUEUE: when we join the touch we sit BEHIND the displayed size at our level
(book_snaps.*_bid_sz). Aggressive same-direction flow first consumes the queue
ahead of us, then fills us. We model this per-market by walking the trade tape in
time order, tracking cumulative hitting-volume since our (re)quote, and only
filling our order once cumulative volume exceeds our queue-ahead. This is the
honest haircut that separates "spread exists" from "I actually get the spread."

We simulate per market over its observed life in the live DB (real book depth,
real trade tape, real settlement). Sweep: which series, quote offset (join touch
vs improve), and a stop-quoting-near-close gate.

Run:
  cd bitcoin && source ../ingest/venv/bin/activate
  PYTHONPATH=$(pwd) python backtest/analysis/explore_marketmaking.py
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DB = "file:livepaper/data/paper.db?mode=ro"
HOURS_SPAN = 3.42  # observed span of the live capture


# ----------------------------- fees -----------------------------
def ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 9)) / 100.0


def taker_fee(p: float) -> float:
    return max(ceil_cent(0.07 * p * (1 - p)), 0.01)


def maker_fee_per_ct(p: float, qty: float) -> float:
    """Maker fee is per-ORDER, ceil-cent of 0.0175*qty*p*(1-p), amortized over qty.
    Big maker orders are cheap per contract because the round-up amortizes."""
    if qty <= 0:
        return 0.0
    order_fee = ceil_cent(0.0175 * qty * p * (1 - p))
    return order_fee / qty


# ----------------------------- data load -----------------------------
def load(con, series_prefix: str):
    tickers = [r[0] for r in con.execute(
        "SELECT DISTINCT ticker FROM trades WHERE ticker LIKE ?",
        (series_prefix + "%",)).fetchall()]
    out = {}
    for tk in tickers:
        res = con.execute("SELECT result FROM windows WHERE ticker=?", (tk,)).fetchone()
        if not res or res[0] not in ("yes", "no"):
            continue  # need a settled outcome to mark inventory
        settle_yes = 1.0 if res[0] == "yes" else 0.0
        tr = pd.read_sql(
            "SELECT ts_ms, sec_to_close, yes_price, no_price, size, taker_side "
            "FROM trades WHERE ticker=? ORDER BY ts_ms", con, params=(tk,))
        bk = pd.read_sql(
            "SELECT ts_ms, sec_to_close, best_yes_bid, yes_bid_sz, best_no_bid, "
            "no_bid_sz, yes_ask FROM book_snaps WHERE ticker=? ORDER BY ts_ms",
            con, params=(tk,))
        if len(tr) < 5 or len(bk) < 5:
            continue
        out[tk] = (tr, bk, settle_yes)
    return out


# ----------------------------- simulator -----------------------------
@dataclass
class Config:
    series: str
    join_touch: bool = True       # True: post AT best bid (join queue). False: improve by 1c.
    queue_frac: float = 1.0       # fraction of displayed queue we sit behind (1.0=full, realistic)
    our_size: float = 10.0        # contracts we quote per side per requote
    stop_secs: float = 0.0        # stop quoting when sec_to_close < this (0 = quote to close)
    min_spread_c: int = 1         # only quote when book spread >= this many cents
    maker_fee_on: bool = True
    inv_cap: float = 1e12         # max |yes_ct - no_ct| inventory skew; stop quoting the heavy side


@dataclass
class Result:
    n_markets: int = 0
    yes_fills: int = 0
    no_fills: int = 0
    yes_ct: float = 0.0
    no_ct: float = 0.0
    gross_spread: float = 0.0      # $ from settlement value of held inventory minus cost
    fees: float = 0.0
    net: float = 0.0
    paired_ct: float = 0.0
    # per-market net for drawdown
    per_market: list = field(default_factory=list)


def simulate_market(tr: pd.DataFrame, bk: pd.DataFrame, settle_yes: float,
                    cfg: Config) -> dict:
    """Walk the trade tape. Maintain a resting YES bid and NO bid at the touch.
    Track queue-ahead consumed by same-direction aggressive flow; fill our order
    once cumulative hitting volume since requote exceeds queue-ahead.
    Re-quote (reset queue) whenever the touch level moves or after a fill.
    """
    bk = bk.sort_values("ts_ms").reset_index(drop=True)
    tr = tr.sort_values("ts_ms").reset_index(drop=True)
    bk_ts = bk["ts_ms"].values

    # resting order state per side: (active, price, queue_ahead_remaining, our_remaining)
    def fresh_quote(side, book_row):
        # side 'yes': we bid to BUY yes at best_yes_bid (join) or +1c (improve)
        if side == "yes":
            b = book_row.best_yes_bid
            q = book_row.yes_bid_sz
            if not cfg.join_touch:
                b = round(b + 0.01, 2)
                q = 0.0  # we are alone at the improved level
        else:
            b = book_row.best_no_bid
            q = book_row.no_bid_sz
            if not cfg.join_touch:
                b = round(b + 0.01, 2)
                q = 0.0
        return {"price": round(b, 2), "queue": q * cfg.queue_frac, "rem": cfg.our_size}

    yes_q = None
    no_q = None
    last_book_i = -1
    inv = 0.0  # running yes_ct - no_ct

    fills = []  # (side, price, qty)

    for _, t in tr.iterrows():
        ts = t.ts_ms
        sec = t.sec_to_close
        # find current book snapshot (most recent <= ts)
        i = np.searchsorted(bk_ts, ts, side="right") - 1
        if i < 0:
            continue
        b = bk.iloc[i]
        if pd.isna(b.yes_ask) or pd.isna(b.best_yes_bid) or pd.isna(b.best_no_bid):
            yes_q = None
            no_q = None
            continue
        spread_c = round((b.yes_ask - b.best_yes_bid) * 100)

        gate_ok = (sec is None) or (sec >= cfg.stop_secs)
        quote_ok = gate_ok and (b.best_yes_bid > 0) and (b.best_no_bid > 0) and \
                   (spread_c >= cfg.min_spread_c) and (b.best_yes_bid < 1.0) and (b.best_no_bid < 1.0)

        # (re)establish quotes if book level moved or we have no quote
        if quote_ok:
            if yes_q is None or i != last_book_i:
                # requote at current touch (reset queue position); keep filling progress only within same level
                newy = fresh_quote("yes", b)
                if yes_q is None or newy["price"] != yes_q["price"]:
                    yes_q = newy
                newn = fresh_quote("no", b)
                if no_q is None or newn["price"] != no_q["price"]:
                    no_q = newn
                last_book_i = i
        else:
            yes_q = None
            no_q = None
            continue

        # inventory cap: if too long YES, stop quoting YES (the side that adds to skew)
        yes_gated = inv >= cfg.inv_cap
        no_gated = inv <= -cfg.inv_cap

        # apply aggressive flow to our resting orders
        if t.taker_side == "no":
            # taker sells yes -> hits resting YES bids at yes_price
            if (not yes_gated) and yes_q is not None and t.yes_price <= yes_q["price"] + 1e-9:
                vol = t.size
                if yes_q["queue"] > 0:
                    eaten = min(yes_q["queue"], vol)
                    yes_q["queue"] -= eaten
                    vol -= eaten
                if vol > 0 and yes_q["rem"] > 0:
                    fillq = min(yes_q["rem"], vol)
                    yes_q["rem"] -= fillq
                    inv += fillq
                    fills.append(("yes", yes_q["price"], fillq))
                    if yes_q["rem"] <= 1e-9:
                        # refill our quote at same level (continuous MM), reset our size, keep queue=0 (we're at front now)
                        yes_q = {"price": yes_q["price"], "queue": 0.0, "rem": cfg.our_size}
        elif t.taker_side == "yes":
            # taker buys yes -> lifts resting YES offers (= our NO bid) at no_price = 1-yes_price
            if (not no_gated) and no_q is not None and (1 - t.yes_price) <= no_q["price"] + 1e-9:
                vol = t.size
                if no_q["queue"] > 0:
                    eaten = min(no_q["queue"], vol)
                    no_q["queue"] -= eaten
                    vol -= eaten
                if vol > 0 and no_q["rem"] > 0:
                    fillq = min(no_q["rem"], vol)
                    no_q["rem"] -= fillq
                    inv -= fillq
                    fills.append(("no", no_q["price"], fillq))
                    if no_q["rem"] <= 1e-9:
                        no_q = {"price": no_q["price"], "queue": 0.0, "rem": cfg.our_size}

    # ----- account for fills: mark to settlement, charge maker fees -----
    yes_ct = sum(q for s, p, q in fills if s == "yes")
    no_ct = sum(q for s, p, q in fills if s == "no")
    yes_cost = sum(p * q for s, p, q in fills if s == "yes")
    no_cost = sum(p * q for s, p, q in fills if s == "no")
    # YES contract pays settle_yes; NO contract pays (1-settle_yes)
    yes_value = settle_yes * yes_ct
    no_value = (1 - settle_yes) * no_ct
    gross = (yes_value - yes_cost) + (no_value - no_cost)

    fees = 0.0
    if cfg.maker_fee_on:
        for s, p, q in fills:
            fees += maker_fee_per_ct(p, q) * q
    net = gross - fees
    paired = min(yes_ct, no_ct)

    # --- paired (true spread) vs inventory decomposition ---
    # Match paired qty: paired pairs earn (1 - avg_yes_px - avg_no_px) each, settling at $1.
    avg_yes_px = (yes_cost / yes_ct) if yes_ct else 0.0
    avg_no_px = (no_cost / no_ct) if no_ct else 0.0
    paired_pnl = paired * (1.0 - avg_yes_px - avg_no_px)  # gross, pre-fee
    inv_pnl = gross - paired_pnl  # leftover one-sided inventory marked to settlement

    return dict(yes_fills=sum(1 for s, *_ in fills if s == "yes"),
                no_fills=sum(1 for s, *_ in fills if s == "no"),
                yes_ct=yes_ct, no_ct=no_ct, gross=gross, fees=fees, net=net,
                paired=paired, n_fills=len(fills),
                paired_pnl=paired_pnl, inv_pnl=inv_pnl,
                avg_yes_px=avg_yes_px, avg_no_px=avg_no_px, fills=fills)


def run_config(markets: dict, cfg: Config) -> Result:
    r = Result()
    for tk, (tr, bk, settle_yes) in markets.items():
        m = simulate_market(tr, bk, settle_yes, cfg)
        r.n_markets += 1
        r.yes_fills += m["yes_fills"]
        r.no_fills += m["no_fills"]
        r.yes_ct += m["yes_ct"]
        r.no_ct += m["no_ct"]
        r.gross_spread += m["gross"]
        r.fees += m["fees"]
        r.net += m["net"]
        r.paired_ct += m["paired"]
        r.per_market.append(m["net"])
    return r


def summarize(cfg: Config, r: Result, hours: float) -> dict:
    total_ct = r.yes_ct + r.no_ct
    fills = r.yes_fills + r.no_fills
    net_per_ct = (r.net / total_ct) if total_ct else 0.0
    gross_per_ct = (r.gross_spread / total_ct) if total_ct else 0.0
    net_per_hr = r.net / hours if hours else 0.0
    net_per_day = net_per_hr * 24
    # drawdown over per-market net sequence (cumulative)
    cum = np.cumsum(r.per_market) if r.per_market else np.array([0.0])
    peak = np.maximum.accumulate(cum)
    dd = float((peak - cum).max()) if len(cum) else 0.0
    return dict(
        series=cfg.series, join=cfg.join_touch, stop=cfg.stop_secs,
        minspr=cfg.min_spread_c, qfrac=cfg.queue_frac, size=cfg.our_size,
        n_mkt=r.n_markets, fills=fills, total_ct=round(total_ct, 0),
        yes_ct=round(r.yes_ct), no_ct=round(r.no_ct), paired=round(r.paired_ct),
        gross=round(r.gross_spread, 2), fees=round(r.fees, 2), net=round(r.net, 2),
        net_per_ct_c=round(net_per_ct * 100, 3),
        gross_per_ct_c=round(gross_per_ct * 100, 3),
        net_per_hr=round(net_per_hr, 3), net_per_day=round(net_per_day, 2),
        ct_per_hr=round(total_ct / hours, 0), dd=round(dd, 2))


def main():
    con = sqlite3.connect(DB, uri=True)
    rows = []
    series_list = ["KXBTC15M", "KXETH15M", "KXBTCD", "KXETHD"]
    data = {s: load(con, s) for s in series_list}
    for s in series_list:
        print(f"{s}: {len(data[s])} settled markets loaded")

    # ---- sweep ----
    for s in series_list:
        mk = data[s]
        if not mk:
            continue
        for join in (True, False):
            for stop in (0.0, 30.0, 60.0):
                for minspr in (1, 2):
                    cfg = Config(series=s, join_touch=join, stop_secs=stop,
                                 min_spread_c=minspr, our_size=10.0, queue_frac=1.0)
                    r = run_config(mk, cfg)
                    rows.append(summarize(cfg, r, HOURS_SPAN))

    df = pd.DataFrame(rows)
    df = df.sort_values("net_per_day", ascending=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    print("\n=== FULL SWEEP (sorted by net $/day, scaled from 3.42h capture) ===")
    print(df.to_string(index=False))

    # Best config detail
    best = df.iloc[0]
    print("\n=== BEST CONFIG ===")
    print(best.to_string())

    # queue-position sensitivity on best series/join
    print("\n=== QUEUE SENSITIVITY (best series, join touch, stop=30) ===")
    bs = best["series"]
    qrows = []
    for qf in (1.0, 0.5, 0.25, 1.5, 2.0):
        cfg = Config(series=bs, join_touch=True, stop_secs=30.0, min_spread_c=1,
                     our_size=10.0, queue_frac=qf)
        r = run_config(data[bs], cfg)
        qrows.append(summarize(cfg, r, HOURS_SPAN))
    print(pd.DataFrame(qrows).to_string(index=False))

    df.to_csv("backtest/analysis/mm_sweep.csv", index=False)
    print("\nwrote backtest/analysis/mm_sweep.csv")


if __name__ == "__main__":
    main()

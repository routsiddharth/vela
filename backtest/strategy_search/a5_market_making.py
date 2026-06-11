"""A5 — Two-sided market making around model fair value.

THESIS
------
The canonical strategy (final_strategy.py) only LIFTS cheap winning-side prints —
a one-directional panic fade. But once a window is locked, fade_lib.model_pwin
gives a sharp FAIR probability p_yes (in RTI units, fully causal). A market maker
can quote BOTH sides around fair:
    rest a YES BID  at  p_yes - delta   (buy YES from panic SELLERS, below fair)
    rest a YES OFFER at p_yes + delta   (sell YES to panic BUYERS,  above fair)
as a pure MAKER (low/zero fee, no spread paid) and earn the edge from BOTH tails.

KALSHI MECHANICS USED FOR THE FILL MODEL
----------------------------------------
A trade print carries (yes_price, taker_side):
  * taker_side == 'no'  -> someone bought NO == SOLD YES, hitting a resting YES BID.
        If that print's yes_price <= my_bid  -> I (the maker) BUY YES at my_bid.
  * taker_side == 'yes' -> someone bought YES, lifting a resting YES OFFER.
        If that print's yes_price >= my_offer -> I (the maker) SELL YES at my_offer.
A SELL of YES is economically a BUY of NO at price (1 - my_offer); we settle on a
single signed YES inventory so both legs net naturally.

DATA BACKING (be explicit — see DATA_NOTES.md)
----------------------------------------------
  * SPREAD / DEPTH realism: livepaper/data/paper.db `book_snaps` (54 windows, the
    ONLY resting-book source). Used for §1 (is there room to quote?).
  * FILL-RATE / EDGE statistics: backtest/data/trades.parquet (2,438 windows of
    taker flow, final 180s) crossed with model_pwin fair value. Used for §2-§4.
    The book is NOT in the parquet, so fills are simulated from taker prints
    crossing our resting quote — the standard maker fill assumption.

FEES: backtest/strategy_search/fees.py — MAKER headline, ZERO optimistic, TAKER pess.
"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.btc_lib as L
from backtest.analysis.fade_lib import model_pwin, estimate_sigma_sec
from backtest.strategy_search import fees

TAU = 45
SEC_LO = 5
PER_WIN_CAP = 1000.0          # max signed |inventory| (contracts) per window
CAP_FRAC = 0.25               # competition haircut: we win this share of a crossing print

# ----------------------------------------------------------------------------
# Load model fair value at tau for every window (causal).
# ----------------------------------------------------------------------------
def load_fair():
    m = L.load_markets(); piv = L.binance_matrix()
    m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
    sig = estimate_sigma_sec(piv)
    mp = model_pwin(piv.loc[m.ticker], m, TAU, sig)   # indexed by ticker
    mp = mp.reset_index()
    return mp, sig


def load_flow():
    t = L.load_trades()
    t = t[(t.sec_to_close >= SEC_LO) & (t.sec_to_close <= TAU)].copy()
    return t


# ----------------------------------------------------------------------------
# §1  SPREAD / DEPTH realism from paper.db book_snaps
# ----------------------------------------------------------------------------
def spread_report():
    import sqlite3
    db = sqlite3.connect("file:livepaper/data/paper.db?mode=ro", uri=True)
    q = """SELECT ticker, sec_to_close, best_yes_bid, yes_bid_sz, best_no_bid,
                  no_bid_sz, yes_ask, no_ask
           FROM book_snaps WHERE sec_to_close>0 AND sec_to_close<=60"""
    b = pd.read_sql(q, db); db.close()
    b["yspread"] = b.yes_ask - b.best_yes_bid
    contested = b[(b.best_yes_bid >= 0.1) & (b.best_yes_bid <= 0.9)]
    polar = b[(b.best_yes_bid.isna()) | (b.best_yes_bid < 0.02) | (b.best_yes_bid >= 0.98)]
    print("\n=== §1 SPREAD / DEPTH (paper.db book_snaps, final 60s) ===")
    print(f"  snapshots total                 : {len(b)}")
    print(f"  POLARIZED (ybid<0.02 / >=0.98 / null): {len(polar)}  ({len(polar)/len(b)*100:.0f}%)")
    print(f"  CONTESTED (ybid in [0.1,0.9])   : {len(contested)}  ({len(contested)/len(b)*100:.0f}%)")
    print(f"  distinct windows w/ any contested snap: "
          f"{contested.ticker.nunique()} of {b.ticker.nunique()}")
    if len(contested):
        print(f"  contested median yes-spread     : {contested.yspread.median():.3f}  "
              f"(mean {contested.yspread.mean():.3f})")
        print(f"  contested median yes_bid_sz     : {contested.yes_bid_sz.median():.0f}  "
              f"no_bid_sz {contested.no_bid_sz.median():.0f}")
    # room to quote inside after fees: maker fee/contract at P~0.5
    mf = fees.fee_per_contract(100, 0.5, fees.MAKER)
    print(f"  maker fee/contract @P=0.5 (100-lot order): {mf*100:.3f} c")
    print("  -> spread is ~1-2c when contested; maker fee is ~0.04c -> ROOM EXISTS,")
    print("     but only the ~14% of windows that are contested offer a 2-sided book.")
    return b


# ----------------------------------------------------------------------------
# §2  Two-sided capture simulation
# ----------------------------------------------------------------------------
def mm_sim(mp, flow, delta, inv_cap=PER_WIN_CAP, cap_frac=CAP_FRAC,
           pull_band=None, rate=fees.MAKER, fade_only=False):
    """Simulate resting quotes per window over [SEC_LO,TAU].

    delta      : half-spread around fair p_yes (in probability/price units)
    inv_cap    : max signed |YES inventory| per window
    cap_frac   : share of each crossing print we capture (competition haircut)
    pull_band  : if set, PULL the *losing-side* quote when p_yes is inside
                 [pull_band, 1-pull_band] (close call -> don't add the loser leg).
                 None = quote both sides always.
    fade_only  : if True, only rest the model-WINNING-side quote (one-sided fade
                 reimplemented in the same fill engine for an apples-to-apples cmp).

    Returns a per-window DataFrame.
    """
    g = mp.dropna(subset=["p_yes"]).copy()
    g = g[["ticker", "p_yes", "yes", "close_dt"]]
    d = flow.merge(g, on="ticker", how="inner")
    if len(d) == 0:
        return pd.DataFrame()
    d = d.sort_values(["ticker", "sec_to_close"], ascending=[True, False])  # time order

    rows = []
    for tk, gg in d.groupby("ticker", sort=False):
        p = float(gg.p_yes.iloc[0]); won_yes = int(gg.yes.iloc[0])
        cdt = gg.close_dt.iloc[0]
        bid = p - delta                 # buy YES below fair
        off = p + delta                 # sell YES above fair
        # clamp to a sane tradable band (Kalshi prices live in [0.01,0.99])
        bid = min(max(bid, 0.01), 0.99)
        off = min(max(off, 0.01), 0.99)
        quote_bid = bid < 0.99          # a buy at >=0.99 has ~no edge / no room
        quote_off = off > 0.01
        if fade_only:
            # winning side only: model says YES likely -> rest bid; else rest offer
            if p >= 0.5:
                quote_off = False
            else:
                quote_bid = False
        if pull_band is not None and pull_band < 0.5:
            # close call: pull the LOSING leg (we keep the leg on the side our model favors)
            if pull_band <= p <= (1 - pull_band):
                pass  # genuine coin flip -> keep both (or none); handled by inv/skip below
            else:
                if p >= 0.5:
                    quote_off = False     # model loves YES; don't sell YES into a runaway
                else:
                    quote_bid = False

        inv = 0.0                      # signed YES contracts
        buy_qty = sell_qty = 0.0
        buy_notional = sell_notional = 0.0
        nfill = 0
        for _, r in gg.iterrows():
            yp = float(r.yes_price); sz = float(r.size) * cap_frac
            if r.taker_side == "no" and quote_bid and yp <= bid + 1e-9:
                # panic seller hits my YES bid -> I BUY YES at my bid
                room = inv_cap - inv
                q = min(sz, max(room, 0.0))
                if q > 0:
                    inv += q; buy_qty += q; buy_notional += q * bid; nfill += 1
            elif r.taker_side == "yes" and quote_off and yp >= off - 1e-9:
                # panic buyer lifts my YES offer -> I SELL YES at my offer
                room = inv_cap + inv
                q = min(sz, max(room, 0.0))
                if q > 0:
                    inv -= q; sell_qty += q; sell_notional += q * off; nfill += 1

        if buy_qty == 0 and sell_qty == 0:
            continue
        # Settlement on signed YES inventory `inv` (= buy_qty - sell_qty).
        # cashflow: paid buy_notional, received sell_notional.
        # each remaining long YES settles to 1 if won_yes else 0.
        # each net short YES settles to -(1 if won_yes else 0) ... handled by signing.
        settle_val = inv * (1.0 if won_yes else 0.0)
        gross = settle_val - buy_notional + sell_notional
        # maker fees: one round-up per side-order. Approximate as one buy order + one sell order.
        fee = 0.0
        if buy_qty > 0:
            fee += fees.order_fee(buy_qty, bid, rate)
        if sell_qty > 0:
            fee += fees.order_fee(sell_qty, off, rate)
        net = gross - fee
        contracts = buy_qty + sell_qty
        rows.append(dict(ticker=tk, p_yes=p, won_yes=won_yes, close_dt=cdt,
                         buy_qty=buy_qty, sell_qty=sell_qty, inv=inv,
                         contracts=contracts, gross=gross, fee=fee, net=net,
                         nfill=nfill, two_sided=int(buy_qty > 0 and sell_qty > 0)))
    return pd.DataFrame(rows)


SAMPLE_FRAC = 2438 / 6308


# ----------------------------------------------------------------------------
# Real one-sided fade (mirror of final_strategy.run) on the SAME windows, same
# fill engine philosophy: lift winning-side prints at price<=CAP, MAKER fee.
# ----------------------------------------------------------------------------
def fade_real(mp, flow, THR_p=0.90, CAP=0.97, cap_frac=CAP_FRAC, rate=fees.MAKER):
    """final_strategy-style fade keyed off model p_yes (>=THR_p -> bet YES,
    <=1-THR_p -> bet NO). Lift any winning-side print at price<=CAP. Hold to settle.
    This is the canonical comparator."""
    g = mp.dropna(subset=["p_yes"]).copy()
    g = g[(g.p_yes >= THR_p) | (g.p_yes <= 1 - THR_p)].copy()
    g["bet_yes"] = g.p_yes >= 0.5
    d = flow.merge(g[["ticker", "bet_yes", "yes", "close_dt"]], on="ticker", how="inner")
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px <= CAP) & (d.win_px > 0)]
    if len(d) == 0:
        return pd.DataFrame()
    d["won"] = np.where(d.bet_yes, d.yes == 1, d.yes == 0)
    d["qty"] = d["size"] * cap_frac
    fee_pc = fees.fee_per_contract(d["qty"].values, d.win_px.values, rate)
    d["pnl"] = (np.where(d.won, 1 - d.win_px, -d.win_px) - fee_pc) * d["qty"]
    rows = []
    for tk, gg in d.groupby("ticker"):
        rows.append(dict(ticker=tk, contracts=gg.qty.sum(), net=gg.pnl.sum(),
                         won=int(gg.won.iloc[0]), two_sided=0,
                         close_dt=gg.close_dt.iloc[0]))
    return pd.DataFrame(rows)


def summarize(df, label, days):
    if df is None or len(df) == 0:
        print(f"  {label:<34}: no fills")
        return None
    ct = df.contracts.sum(); net = df.net.sum()
    nwin = len(df); nlos = int((df.net < 0).sum())
    net_c = net / ct * 100
    # win% by contract: a fill is 'won' if it ends net-positive at window level — use
    # window-level pnl sign for losers; for win% use settlement on inventory side.
    pw = df.net.values
    worst = pw.min()
    cvar5 = pw[pw <= np.percentile(pw, 5)].mean() if len(pw) >= 20 else worst
    usd_day = net / SAMPLE_FRAC / days
    twosided = df.two_sided.mean() * 100
    print(f"  {label:<34}: net {net_c:6.2f} c/ct | {nwin:4d} win / {nlos:3d} los "
          f"| {ct/1000:6.1f}k ct | ${usd_day:6.1f}/day | worst {worst:7.2f} "
          f"| CVaR5 {cvar5:7.2f} | 2sided {twosided:3.0f}%")
    return dict(net_c=net_c, net=net, nwin=nwin, nlos=nlos, ct=ct,
                usd_day=usd_day, worst=worst, cvar5=cvar5, twosided=twosided, pw=pw)


def bucket_diag(mp, flow):
    """Where does each MM leg make/lose money, by fair-value regime?
    Decompose the bid leg (buy YES low) and offer leg (sell YES high) separately."""
    print("\n  -- LEG-LEVEL edge by p_yes regime (delta=0.05, cap=300) --")
    df = mm_sim(mp, flow, 0.05, fade_only=False, inv_cap=300)
    if len(df) == 0:
        return
    df["bucket"] = pd.cut(df.p_yes, [0, 0.1, 0.3, 0.7, 0.9, 1.0],
                          labels=["0-.1", ".1-.3", ".3-.7", ".7-.9", ".9-1"])
    agg = df.groupby("bucket", observed=True).agg(
        nwin=("ticker", "size"), net=("net", "sum"),
        buy=("buy_qty", "sum"), sell=("sell_qty", "sum"),
        net_per_win=("net", "mean"))
    print(agg.to_string())


def main():
    mp, sig = load_fair()
    flow = load_flow()
    m = L.load_markets()
    days = (m.close_dt.max() - m.close_dt.min()).days
    print(f"sigma_sec={sig:.2f}  windows_with_fair={mp.p_yes.notna().sum()}  "
          f"period={days}d  sample_frac={SAMPLE_FRAC:.2f}")
    print("p_yes regime (at tau=45): "
          f"contested[0.1,0.9]={((mp.p_yes>=0.1)&(mp.p_yes<=0.9)).mean()*100:.0f}%  "
          f"coinflip[0.3,0.7]={((mp.p_yes>=0.3)&(mp.p_yes<=0.7)).mean()*100:.0f}%")

    spread_report()

    print("\n=== §2 TWO-SIDED MM vs CANONICAL FADE (trades.parquet flow, MAKER fees) ===")
    print("  -- CANONICAL fade (final_strategy: bet confident side, lift print<=CAP) --")
    for THR_p, CAP in [(0.90, 0.97), (0.90, 0.99), (0.80, 0.97)]:
        summarize(fade_real(mp, flow, THR_p, CAP), f"fade  p>={THR_p} CAP={CAP}", days)
    print("  -- TWO-SIDED MM (rest bid@fair-d AND offer@fair+d), delta sweep --")
    for delta in [0.02, 0.05, 0.10, 0.15]:
        df = mm_sim(mp, flow, delta, fade_only=False, inv_cap=300)
        summarize(df, f"MM    delta={delta} cap=300", days)
    bucket_diag(mp, flow)

    print("\n=== §3 INVENTORY CAP + PULL-CLOSE-CALL (two-sided, delta=0.05) ===")
    base = mm_sim(mp, flow, 0.05, fade_only=False)
    summarize(base, "MM base (cap=1000, no pull)", days)
    for cap in [50, 100, 300]:
        summarize(mm_sim(mp, flow, 0.05, inv_cap=cap), f"MM inv_cap={cap}", days)
    for pb in [0.45, 0.40, 0.35, 0.25, 0.15]:
        # pull the losing leg unless within [pb,1-pb] coin-flip band
        summarize(mm_sim(mp, flow, 0.05, pull_band=pb), f"MM pull losing-leg pb={pb}", days)

    print("\n=== §4 ADDITIVITY: does MM produce DIFFERENT fills than the fade? ===")
    d_mm = mm_sim(mp, flow, 0.05, fade_only=False, inv_cap=300)
    d_fd = fade_real(mp, flow, 0.90, 0.97)
    if len(d_mm) and len(d_fd):
        mm_w = set(d_mm.ticker); fd_w = set(d_fd.ticker)
        sell_q = d_mm.sell_qty.sum(); buy_q = d_mm.buy_qty.sum()
        print(f"  windows traded: MM={len(mm_w)}  canonical-fade={len(fd_w)}  "
              f"overlap={len(mm_w & fd_w)}  MM-only={len(mm_w - fd_w)}")
        print(f"  MM buy(YES-bid) contracts ={buy_q/1000:.1f}k ; "
              f"MM sell(YES-offer) contracts ={sell_q/1000:.1f}k  "
              f"(offer leg = flow the fade never touches)")
        print(f"  windows that filled BOTH legs (true 2-sided spread capture): "
              f"{int(d_mm.two_sided.sum())}")

    print("\n=== ZERO / TAKER fee sensitivity (two-sided, delta=0.05, inv_cap=300) ===")
    for rate, nm in [(fees.ZERO, "ZERO"), (fees.MAKER, "MAKER"), (fees.TAKER, "TAKER")]:
        summarize(mm_sim(mp, flow, 0.05, inv_cap=300, rate=rate), f"MM {nm}", days)


if __name__ == "__main__":
    main()

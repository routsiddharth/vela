"""explore_ladderarb.py — Cross-strike STRUCTURAL ARBITRAGE scan on Kalshi
hourly crypto ladders (KXBTCD / KXETHD).

The ~100 strikes of one hourly event ("BTC > $K at top of hour?") must obey
no-arbitrage across the ladder:

  (A) MONOTONICITY  — P(settle>K) decreases in K. A locked arb exists iff you
      can BUY the YES of a HIGHER strike cheaper than you can SELL the YES of a
      LOWER strike, i.e.  yes_ask(K_hi) < yes_bid(K_lo)  for K_hi > K_lo.
      You buy YES@K_hi (ask) and sell YES@K_lo (= buy NO@K_lo at no_ask).
      If settle>K_hi: both pay (you're +1 on the long, -1 on the short) -> net 0 on payout.
      If K_lo<settle<=K_hi: long loses (0), short wins for you... let's be precise below.
      Cleanest framing: buying YES@K_hi and NO@K_lo (K_hi>K_lo) is a box that ALWAYS
      pays exactly $1 in the region settle<=K_lo or settle>K_hi, and ... -> we use the
      strict locked form: yes_ask(K_hi) + no_ask(K_lo) < 1  with K_hi>K_lo guarantees >=$1 payout? NO.
      We instead use the rigorous monotone-bid form (see below) which is a true lock.

  (B) YES/NO INTERNAL CROSS (single market) — buy YES and NO of the SAME market for
      < $1: yes_ask + no_ask < 1  => locked $1 payout for < $1 cost. (riskless)

  (C) VERTICAL / BUTTERFLY density — implied density from 3 adjacent strikes must be
      >= 0. Negative butterfly = mispricing (model-light structural).

  (D) MONOTONE-ASK lock — the true cross-strike lock. For K_hi > K_lo, a YES that
      is HIGHER-strike must cost <= a lower-strike YES. If yes_ask(K_hi) < yes_bid(K_lo):
        SELL YES@K_lo  (receive yes_bid(K_lo)), BUY YES@K_hi (pay yes_ask(K_hi)).
        Net credit = yes_bid(K_lo) - yes_ask(K_hi) > 0.
        Payoffs: settle>K_hi -> +1 (long) -1 (short) = 0. K_lo<settle<=K_hi -> 0 -1 = -1.
        settle<=K_lo -> 0 - 0 = 0.  So worst case you LOSE $1 in the middle band -> NOT riskless.
      => pure monotonicity-of-ASK is NOT a riskless lock by itself. The riskless lock is (B)
         and the box (E).

  (E) BOX (true riskless cross-strike): For K_lo < K_hi, the spread
        "YES@K_lo minus YES@K_hi" pays exactly $1 if K_lo<settle<=K_hi, else $0  -> it's a
        long binary call spread, value in [0,1], cost = yes_ask(K_lo)-yes_bid(K_hi).
        Riskless arb iff cost < 0 (you get PAID to hold a non-negative payoff):
            yes_bid(K_hi) > yes_ask(K_lo)  with K_hi>K_lo  -> SELL YES@K_hi, BUY YES@K_lo, net credit, payoff>=0. LOCK.
        This is exactly the monotonicity-of-bid-vs-ask violation in the prompt. We scan it.

We net out TAKER fees on every leg:
    taker_fee/contract = ceil_cent(0.07 * p * (1-p)), min $0.01.

We poll the LIVE full ladder via Kalshi REST repeatedly over a window to get many
(timestamp x ladder) snapshots, then scan every snapshot for (B), (C), (E).

Output: per-violation-type frequency, available size (depth), edge after fees, and a
$/day projection. Honest netting.
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kalshi_client import Kalshi  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "findings"
SNAP_FILE = Path(__file__).resolve().parent.parent / "data" / "ladder_snaps.jsonl"


def ceil_cent(x: float) -> float:
    return math.ceil(round(x * 100, 6)) / 100.0


def taker_fee(p: float) -> float:
    """Per-contract taker fee in dollars at price p (0..1)."""
    return max(0.01, ceil_cent(0.07 * p * (1 - p)))


# ----------------------------------------------------------------------------
# Collection: poll full ladders for all open BTCD/ETHD events.
# ----------------------------------------------------------------------------
def fetch_ladders(k: Kalshi, series: list[str]) -> list[dict]:
    """Return list of {series,event,close_time,ts, strikes:[{K,yb,ya,nb,na,ybs,...}]}."""
    out = []
    ts = time.time()
    for s in series:
        m = k.get("/markets", {"series_ticker": s, "status": "open", "limit": 1000})
        ev = defaultdict(list)
        for x in m.get("markets", []):
            ev[x["event_ticker"]].append(x)
        for et, xs in ev.items():
            strikes = []
            for x in xs:
                yb = x.get("yes_bid_dollars")
                ya = x.get("yes_ask_dollars")
                nb = x.get("no_bid_dollars")
                na = x.get("no_ask_dollars")
                strikes.append(
                    dict(
                        ticker=x["ticker"],
                        K=x.get("floor_strike"),
                        yb=float(yb) if yb is not None else None,
                        ya=float(ya) if ya is not None else None,
                        nb=float(nb) if nb is not None else None,
                        na=float(na) if na is not None else None,
                        # top-of-book sizes (contracts), fp -> /1e? Kalshi fp is *100? we store raw.
                        ybs=x.get("yes_bid_size_fp"),
                        yas=x.get("yes_ask_size_fp"),
                    )
                )
            strikes = [s2 for s2 in strikes if s2["K"] is not None]
            strikes.sort(key=lambda d: d["K"])
            out.append(
                dict(series=s, event=et, close_time=xs[0]["close_time"], ts=ts, strikes=strikes)
            )
    return out


def collect(minutes: float, interval: float = 6.0) -> None:
    k = Kalshi()
    series = ["KXBTCD", "KXETHD"]
    SNAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    t_end = time.time() + minutes * 60
    n = 0
    with SNAP_FILE.open("a") as f:
        while time.time() < t_end:
            try:
                lads = fetch_ladders(k, series)
                for lad in lads:
                    f.write(json.dumps(lad) + "\n")
                f.flush()
                n += len(lads)
                print(f"[{time.strftime('%H:%M:%S')}] wrote {len(lads)} ladders (total {n})")
            except Exception as e:  # noqa: BLE001
                print("fetch err:", e)
            time.sleep(interval)


# ----------------------------------------------------------------------------
# Scanning a single ladder snapshot.
# ----------------------------------------------------------------------------
def scan_ladder(lad: dict) -> list[dict]:
    """Return list of violation dicts found in this ladder snapshot."""
    vios = []
    strikes = [s for s in lad["strikes"]]
    meta = dict(event=lad["event"], ts=lad["ts"], close_time=lad["close_time"])

    # (B) YES/NO internal cross on a single market: yes_ask + no_ask < 1.
    for s in strikes:
        ya, na = s["ya"], s["na"]
        if ya is None or na is None:
            continue
        cost = ya + na
        if cost < 1.0:
            # buy 1 YES + 1 NO -> guaranteed $1 payout. fees on both legs (taker).
            fee = taker_fee(ya) + taker_fee(na)
            net = 1.0 - cost - fee
            vios.append({**meta, "type": "B_yesno_cross", "K": s["K"],
                         "cost": cost, "fee": fee, "net_edge": net})

    # (E) BOX riskless cross-strike: for K_hi>K_lo, yes_bid(K_hi) > yes_ask(K_lo).
    #     SELL YES@K_hi (recv bid), BUY YES@K_lo (pay ask). payoff in {0, ...} always >=0,
    #     net credit = yes_bid(K_hi) - yes_ask(K_lo) - fees.
    #     We only need to check ADJACENT inversions plus the global max over a window,
    #     but the cleanest is: any pair K_lo<K_hi with yb(K_hi) > ya(K_lo).
    # A *tradeable* YES quote needs a real two-sided market: 0 < ya < 1 and 0 < yb < 1
    # plus ya >= yb (a real spread). ya==0 or yb==0 or ya==1 are PHANTOM book levels on
    # far-from-money illiquid strikes (no order posted -> default fill of the implied side),
    # NOT executable. Excluding them is the whole game: every "violation" before this
    # filter is one of these phantoms.
    def real(s):
        return (s["ya"] is not None and s["yb"] is not None
                and 0.0 < s["yb"] < 1.0 and 0.0 < s["ya"] < 1.0
                and s["ya"] >= s["yb"])

    quoted = [s for s in strikes if real(s)]
    for i in range(len(quoted)):
        for j in range(i + 1, len(quoted)):
            lo, hi = quoted[i], quoted[j]  # lo.K < hi.K
            yb_hi, ya_lo = hi["yb"], lo["ya"]
            if yb_hi is None or ya_lo is None:
                continue
            if yb_hi > ya_lo:
                fee = taker_fee(yb_hi) + taker_fee(ya_lo)
                net = (yb_hi - ya_lo) - fee
                vios.append({**meta, "type": "E_box_cross",
                             "K_lo": lo["K"], "K_hi": hi["K"],
                             "credit": yb_hi - ya_lo, "fee": fee, "net_edge": net})

    # (C) BUTTERFLY: density from 3 adjacent strikes must be >=0.
    #     Using MID prices p(K) = P(settle>K). For equal strike spacing h,
    #     d2 = p(K-h) - 2 p(K) + p(K+h) ; density ~ -d2 / h must be >=0 => d2 <= 0.
    #     A *tradeable* butterfly arb: BUY 1 YES@K-h, SELL 2 YES@K, BUY 1 YES@K+h for a
    #     credit, payoff is the (always >=0) butterfly. Riskless iff executable cost<0.
    #     Cost (taker) = ya(K-h) - 2*yb(K) + ya(K+h). Arb iff cost + fees < 0.
    for i in range(1, len(quoted) - 1):
        a, b, c = quoted[i - 1], quoted[i], quoted[i + 1]
        # require ~equal spacing
        if abs((b["K"] - a["K"]) - (c["K"] - b["K"])) > 1e-6:
            continue
        if a["ya"] is None or c["ya"] is None or b["yb"] is None:
            continue
        # BUY YES@a (pay ask), SELL 2 YES@b (recv bid), BUY YES@c (pay ask).
        credit = -a["ya"] + 2 * b["yb"] - c["ya"]  # cash at entry (can be + or -)
        fee = taker_fee(a["ya"]) + 2 * taker_fee(b["yb"]) + taker_fee(c["ya"])
        # WORST-CASE payoff across the 4 settlement regions (S<=a, a<S<=b, b<S<=c, S>c):
        #   region S<=a : 0           region a<S<=b : +1
        #   region b<S<=c : -1        region S>c   : 0
        # min payoff = -1 ALWAYS for this construction -> riskless only if credit-fee-1 >= 0,
        # i.e. credit > 1 (impossible). So a "negative density" here is NOT riskless.
        worst_payoff = -1.0  # the b<S<=c region
        net = credit + worst_payoff - fee  # guaranteed floor; >0 => true lock
        if net > 0:
            vios.append({**meta, "type": "C_butterfly", "K": b["K"],
                         "credit": credit, "fee": fee, "net_edge": net})

    # sanity counters for monotonicity of MID (diagnostic, not a trade)
    return vios


def diag_monotonicity(lad: dict) -> dict:
    quoted = [s for s in lad["strikes"]
              if s["ya"] is not None and s["yb"] is not None
              and 0.0 < s["yb"] < 1.0 and 0.0 < s["ya"] < 1.0 and s["ya"] >= s["yb"]]
    mids = [(s["K"], (s["yb"] + s["ya"]) / 2) for s in quoted]
    invs = sum(1 for i in range(len(mids) - 1) if mids[i + 1][1] > mids[i][1] + 1e-9)
    return dict(event=lad["event"], n_quoted=len(quoted), mid_inversions=invs)


# ----------------------------------------------------------------------------
def analyze() -> None:
    if not SNAP_FILE.exists():
        print("no snapshot file:", SNAP_FILE)
        return
    lads = [json.loads(line) for line in SNAP_FILE.read_text().splitlines() if line.strip()]
    print(f"loaded {len(lads)} ladder snapshots")
    # time span
    tss = [l["ts"] for l in lads]
    span_h = (max(tss) - min(tss)) / 3600 if len(tss) > 1 else 0
    n_events = len({l["event"] for l in lads})
    print(f"span={span_h:.2f}h  distinct events={n_events}  distinct ladder-snaps={len(lads)}")

    all_vios = []
    mono_inv_total = 0
    mono_snaps = 0
    for lad in lads:
        all_vios.extend(scan_ladder(lad))
        d = diag_monotonicity(lad)
        mono_inv_total += d["mid_inversions"]
        mono_snaps += 1

    by_type = defaultdict(list)
    for v in all_vios:
        by_type[v["type"]].append(v)

    print("\n=== RAW violations (before requiring net_edge>0 already applied) ===")
    print(f"mid-price monotonicity inversions across {mono_snaps} ladder-snaps: "
          f"{mono_inv_total} total ({mono_inv_total/max(1,mono_snaps):.2f}/ladder)")
    for t in ["B_yesno_cross", "E_box_cross", "C_butterfly"]:
        vs = by_type.get(t, [])
        pos = [v for v in vs if v["net_edge"] > 0]
        print(f"\n[{t}] {len(vs)} found, {len(pos)} survive spread+fees (net_edge>0)")
        if pos:
            edges = sorted(v["net_edge"] for v in pos)
            print(f"   net_edge $/contract: min={edges[0]:.4f} med={edges[len(edges)//2]:.4f} "
                  f"max={edges[-1]:.4f}  sum={sum(edges):.4f}")
            for v in pos[:8]:
                print("   ", {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                              for kk, vv in v.items() if kk not in ("ts", "close_time")})

    # $/day projection: surviving net edges are per-contract; multiply by an assumed
    # fillable size. We do NOT know fill size from top-of-book reliably, so report
    # per-event and per-ladder-snap rates and let size be a separate factor.
    surviving = [v for v in all_vios if v["net_edge"] > 0]
    print("\n=== SUMMARY ===")
    print(f"total surviving riskless violations: {len(surviving)} over {len(lads)} ladder-snaps "
          f"({span_h:.2f}h)")
    if surviving:
        total_edge = sum(v["net_edge"] for v in surviving)
        print(f"sum of per-contract net edge: ${total_edge:.4f}")
        if span_h > 0:
            print(f"per-hour: {len(surviving)/span_h:.2f} violations/h, "
                  f"${total_edge/span_h:.4f}/h per-contract  -> "
                  f"${24*total_edge/span_h:.2f}/day per-contract (1 ct each)")
    else:
        print("ZERO riskless violations survive spread+fees.")

    OUT.mkdir(exist_ok=True)
    (OUT / "ladderarb_raw.json").write_text(json.dumps(
        {"n_lads": len(lads), "span_h": span_h, "n_surviving": len(surviving),
         "mono_inv_total": mono_inv_total, "by_type_counts":
             {t: len(v) for t, v in by_type.items()},
         "surviving_sample": surviving[:50]}, indent=2))
    print(f"\nwrote {OUT/'ladderarb_raw.json'}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "analyze", "snapshot"])
    ap.add_argument("--minutes", type=float, default=8.0)
    ap.add_argument("--interval", type=float, default=6.0)
    args = ap.parse_args()
    if args.cmd == "collect":
        collect(args.minutes, args.interval)
    elif args.cmd == "snapshot":
        # one-shot single fetch (quick test)
        k = Kalshi()
        lads = fetch_ladders(k, ["KXBTCD", "KXETHD"])
        SNAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SNAP_FILE.open("a") as f:
            for lad in lads:
                f.write(json.dumps(lad) + "\n")
        print(f"wrote {len(lads)} ladders")
    else:
        analyze()

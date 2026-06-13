"""Run the multi-market live paper-trader:  python -m livepaper   (from bitcoin/)

Trades every series in config.MARKETS (BTC15M + ETH15M + BTC hourly) off per-asset
Binance feeds + de-bias. Stop with Ctrl-C; everything flushes to data/paper.db.
Inspect anytime: python -m livepaper.report
"""
from __future__ import annotations
import asyncio, signal, sys
from datetime import datetime, timezone

from . import config as C
from .store import Store
from .feeds import BinanceFeed, KalshiWS
from .market import Discovery, Debias
from .engine import Engine

try:
    from backtest.kalshi_client import Kalshi
except ModuleNotFoundError:
    sys.path.insert(0, str(C.ROOT.parent))
    from backtest.kalshi_client import Kalshi


def make_logger():
    fh = open(C.LOG_PATH, "a")
    def log(msg: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
        print(line, flush=True)
        fh.write(line + "\n"); fh.flush()
    return log


async def periodic(fn, every: float, stop: asyncio.Event, in_thread=False):
    while not stop.is_set():
        try:
            await asyncio.to_thread(fn) if in_thread else fn()
        except Exception as e:
            print(f"[loop error] {getattr(fn,'__name__',fn)}: {e}", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    store = Store()
    log = make_logger()
    log("=== livepaper starting — multi-market TWAP panic-fade ===")
    log(f"markets={[m['series'] for m in C.MARKETS]} bankroll=${C.BANKROLL:.0f} "
        f"p_side>={C.P_SIDE_MIN_BY_ASSET} (default {C.P_SIDE_MIN}) "
        f"px=[{C.WIN_PX_FLOOR},{C.CAP}] window=[{C.SEC_LO},{C.SEC_HI}]s "
        f"sizing={C.PORTFOLIO_FRACTION:.0%}-of-portfolio/trade(round-up) "
        f"maker_fee={C.MAKER_FEE_RATE}")

    kalshi = Kalshi()
    disc = Discovery(kalshi)
    feed = BinanceFeed(store, log, list(C.ASSET_SYMBOL.values()))
    debias = {a: Debias(a, sym) for a, sym in C.ASSET_SYMBOL.items()}
    meta = {m["series"]: {"asset": m["asset"], "symbol": m["symbol"]} for m in C.MARKETS}
    engine = Engine(store, feed, disc, debias, meta, log)

    callbacks = {"snapshot": engine.on_snapshot, "delta": engine.on_delta,
                 "trade": engine.on_trade, "lifecycle": engine.on_lifecycle}
    ws = KalshiWS(store, log, callbacks)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # bootstrap each asset's de-bias from its 15M series (blocking REST -> threads)
    def boot():
        for a, db in debias.items():
            db.bootstrap(disc, store, log, C.ASSET_DEBIAS_SERIES[a])
    await asyncio.to_thread(boot)

    def discover():
        actives = []
        for m in C.MARKETS:
            band, sym = m["band"], m["symbol"]
            spot = feed.latest(sym)[1] if feed.latest(sym) else None
            for d in disc.active(m["series"]):
                # laddered series: keep only strikes near spot (where panic-fade fires)
                if band is not None:
                    if spot is None or abs(d["strike"] - spot) / spot > band:
                        continue
                d.update(asset=m["asset"], symbol=sym, series=m["series"])
                actives.append(d)
        ws.set_desired(engine.sync_markets(actives))
    await asyncio.to_thread(discover)

    def status():
        n_open = sum(1 for s in engine.states.values() if not s.settled)
        deltas = " ".join(f"{a}=${db.delta():.2f}" for a, db in debias.items())
        prices = " ".join(f"{sym}={feed.latest(sym)[1] if feed.latest(sym) else '?'}"
                          for sym in C.ASSET_SYMBOL.values())
        log(f"status bal=${engine.cash:.2f} realized=${engine.realized:+.3f} "
            f"tracking={n_open} trades_seen={engine.n_trades} | delta[{deltas}] | {prices}")

    tasks = [
        asyncio.create_task(feed.run(stop), name="binance"),
        asyncio.create_task(ws.run(stop), name="kalshi"),
        asyncio.create_task(periodic(discover, C.DISCOVERY_EVERY, stop, in_thread=True), name="discover"),
        asyncio.create_task(periodic(engine.tick, C.TICK, stop), name="tick"),
        asyncio.create_task(periodic(engine.settle_closed, C.SETTLE_POLL_EVERY, stop, in_thread=True), name="settle"),
        asyncio.create_task(periodic(status, 60.0, stop), name="status"),
    ]
    await stop.wait()
    log("shutting down — flushing store...")
    if engine.live is not None:
        engine.live.shutdown()      # cancel all resting REAL orders before exit
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    store.flush(); status(); store.close()
    log(f"=== stopped. final balance ${engine.cash:.2f} (realized ${engine.realized:+.3f}) ===")


if __name__ == "__main__":
    asyncio.run(main())

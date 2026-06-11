"""Step 2: WHY do cheap fills win at tau=45 but lose at tau=30/60?
Characterize the cheap-fill windows with pre-trade-observable features and inspect
the tau=30 losers vs tau=45 winners. Build a causal filter.

Hypothesis: cheap fills are someone DUMPING the soon-to-win side. At tau where
enough settlement seconds are locked, the dump is wrong (panic) -> we win. At tau
with few locked seconds, the dump may be INFORMED (spot genuinely reversing through
strike) -> the model is stale and we lose.

A causal discriminator we can compute at decision time: the MARGIN OF SAFETY of the
locked samples alone. If the already-locked settlement seconds put S firmly on our
side regardless of the remaining seconds, a cheap offer is free money."""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.btc_lib as L
import backtest.analysis.liq_common as C

def locked_only_margin(piv, tau, delta, strike):
    """Causal: using ONLY the locked settlement seconds [tau,60], what is the
    worst-case settlement margin if the remaining (tau-1) seconds came in at the
    LEAST favorable plausible value? We compute the partial locked average and a
    conservative bound. Returns (locked_avg_margin, n_locked)."""
    if tau > 60:
        return pd.Series(np.nan, index=piv.index), 0
    locked_cols=[s for s in range(tau,61)]
    n_lock=len(locked_cols)
    locked_avg = piv[locked_cols].mean(axis=1) - delta   # RTI units, avg of locked secs
    return locked_avg - strike, n_lock

def main():
    mdl = C.build_model()
    piv = L.binance_matrix()
    m = L.load_markets().set_index('ticker')
    raw60=L.raw_avg60(piv);
    mser=L.load_markets()
    delta=L.causal_bias(mser, raw60)
    delta_t=pd.Series(delta.values,index=mser['ticker']).reindex(piv.index)
    trades=C.load_trades_cached()

    for tau in [30,45,60]:
        f=C.winning_side_fills(trades,mdl,tau,50)
        cheap=f[f.price<=0.97]
        if len(cheap)==0:
            print(f'tau={tau}: no cheap'); continue
        cw=cheap.ticker.unique()
        lm,nlock=locked_only_margin(piv.loc[cw],tau,delta_t.loc[cw],m.loc[cw,'strike'])
        # signed locked margin in the PREDICTED direction
        pred_yes=mdl.loc[cw,f'mhat_{tau}']>=0
        signed_lm = np.where(pred_yes, lm, -lm)   # >0 means locked secs favor our side
        won=cheap.groupby('ticker')['won'].first().reindex(cw).values
        df=pd.DataFrame({'ticker':cw,'signed_locked_margin':signed_lm,'won':won})
        print(f'=== tau={tau} (n_locked_secs={nlock}) cheap(<=0.97) windows={len(cw)} ===')
        print(df.sort_values('signed_locked_margin').to_string(index=False))
        # does signed_locked_margin>0 perfectly separate winners?
        pos=df[df.signed_locked_margin>0]; neg=df[df.signed_locked_margin<=0]
        pw = pos.won.mean() if len(pos) else float('nan')
        nw = neg.won.mean() if len(neg) else float('nan')
        print(f'  windows locked_margin>0: {len(pos)} won={pw}')
        print(f'  windows locked_margin<=0: {len(neg)} won={nw}')
        print()

if __name__=='__main__':
    main()

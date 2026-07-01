# BTC config-regime map (Step 4)

Derived from `STRATEGY.md` change-log, confirmed against `orders`/`events`.
**Entry logic is constant across the whole live period** (p_side gate **0.84**,
strong-take **0.95**). The only changes are sizing and the v1→v2 endpoint dead window.
Boundaries are in `analysis/regimes.py` (`label()`, `keep()`).

| Regime | Window (UTC) | Placements | Keep? | What |
|---|---|---|---|---|
| **R0_fixed5** | live start → 06-18 21:45 | 214 | ✅ | gate 0.84, strong 0.95, ~5 contracts (ledger ~$50). Orders working. |
| **DEAD_410** | 06-18 21:45 → 06-20 07:56 | **0** | ❌ | v1 endpoint HTTP 410 → orders **silently failed**. 06-19 = 0 fills, 101 errors. Exclude. |
| **R1_stable** | 06-20 07:56 → end (06-28) | 1142 | ✅ | v2 endpoint, gate 0.84, strong 0.95, **dynamic 10% sizing** (5.7→8.8 ct as ledger grew). The clean frozen-config bulk (~8 days). |

Total placements = 214 + 1142 = **1,356** (none fall in the dead window — naturally excluded).

## How to use
- **Fill model (step 3):** pool R0+R1 (same entry logic); add a sizing/regime covariate.
  Exclude DEAD_410 (no real orders there anyway).
- **Significance / attribution:** report on **R1_stable** as the clean frozen-config period,
  and separately on R0+R1 pooled. Never pool across DEAD_410.
- The sizing switch ($5 → 10%) barely affects P(fill) early (≈5 contracts at the $50 start);
  it only diverges as the ledger compounds. Keep it as a covariate, not a hard split.

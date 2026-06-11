"""Canonical Kalshi fee model for the strategy search — USE THIS, not the old
per-contract 1c-floor model in engine.py / analyze_caps.py / final_strategy.py.

Kalshi's published schedule (confirmed from the exchange fee page, 2026-06):

  TAKER fee (order immediately matched):  round_up_to_cent(0.07   * C * P * (1-P))
  MAKER fee (resting order, listed mkts): round_up_to_cent(0.0175 * C * P * (1-P))
  Resting orders NOT in the Maker-Fees list: NO FEE.

  C = number of contracts in the ORDER (inside the round-up -> ONE round-up per
      order, NOT per contract). P = price in dollars (0.50 = 50c).

WHY THIS MATTERS FOR US:
  This strategy RESTS cheap bids and lets panic sellers hit them -> WE ARE THE
  MAKER. The panic seller pays the 0.07 taker fee; we pay the 0.0175 maker fee
  (or zero if the product isn't in the maker-fee list). The old code charged
  0.07 AND a per-CONTRACT 1c floor -- doubly wrong: wrong rate, wrong role, and
  rounding each contract up to 1c instead of rounding the whole order once.

  Example: capture a 100-lot at P=0.97.
    old code:  100 * max(0.01, ceil(0.07*0.97*0.03*100)/100) = 100 * 0.01 = $1.00
    maker:     ceil(0.0175*100*0.97*0.03*100)/100            = $0.06   (~16x less)
    zero:      $0.00

Report results under MAKER (the realistic case) as the headline, with ZERO as
the optimistic bound and TAKER as the pessimistic / old baseline.
"""
from __future__ import annotations
import numpy as np


def _round_up_cent(dollars):
    return np.ceil(np.asarray(dollars, dtype=float) * 100.0) / 100.0


def order_fee(qty, price, rate: float) -> float:
    """Fee in DOLLARS for one order of `qty` contracts at `price` (one round-up).
    rate: 0.07 taker, 0.0175 maker, 0.0 fee-free resting."""
    return float(_round_up_cent(rate * np.asarray(qty) * price * (1.0 - price)))


def order_fee_vec(qty, price, rate: float):
    """Vectorized: qty, price arrays -> per-ORDER fee dollars (one round-up each)."""
    return _round_up_cent(rate * np.asarray(qty, float) * np.asarray(price, float)
                          * (1.0 - np.asarray(price, float)))


def fee_per_contract(qty, price, rate: float):
    """Amortized fee per contract = order_fee / qty. This is the number to compare
    against gross (1-price) per contract."""
    qty = np.asarray(qty, float)
    return np.where(qty > 0, order_fee_vec(qty, price, rate) / np.maximum(qty, 1e-9), 0.0)


# rate constants
TAKER = 0.07
MAKER = 0.0175
ZERO = 0.0


# ---- the OLD (wrong) model, kept only for side-by-side comparison ------------
def old_fee_per_contract(price):
    """engine.py's model: per-contract, ceil to cent, 1c floor. Overcharges."""
    p = np.asarray(price, float)
    return np.maximum(0.01, np.ceil(0.07 * p * (1.0 - p) * 100.0) / 100.0)

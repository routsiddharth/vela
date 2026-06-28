"""Trading service — Kalshi discovery + book, the settlement projection, the gate/
decision engine, execution, and the ledger. Consumes the PriceBlend bundle; never
reads the Binance feed or de-bias tracker directly. See MIGRATION_PLAN.md."""
from .book import OrderBook, MarketState
from .discovery import Discovery
from .kalshi_ws import KalshiWS
from .projection import project, Projection
from .portfolio import SharedPortfolio
from .broker import LiveBroker, MockBroker, new_client_order_id, px_cents
from .live_exec import LiveExecutor
from .engine import Engine

__all__ = ["OrderBook", "MarketState", "Discovery", "KalshiWS", "project",
           "Projection", "SharedPortfolio", "LiveBroker", "MockBroker",
           "new_client_order_id", "px_cents", "LiveExecutor", "Engine"]

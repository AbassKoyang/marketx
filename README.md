# MarketX Backend

MarketX is a campus-focused simulated prediction-market backend. Balances,
orders, positions, P&L, and payouts are virtual credits only. The backend does
not implement real-money deposits, withdrawals, crypto transfers, or payment
processing for trading balances.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

For PostgreSQL, set `DATABASE_URL` in `.env`, for example:

```text
DATABASE_URL=postgres://marketx:marketx@localhost:5432/marketx
```

Redis-backed workers:

```powershell
celery -A config worker -l info
celery -A config beat -l info
```

## Core Accounting

All monetary-like values are integers. `CREDIT_SCALE = 10000`, so `10000`
units equals one virtual payout credit. Prices are integer ticks from `1` to
`9999`; a contract pays `10000` units if its outcome wins. Quantities are whole
contracts.

Every market has a single price vector calculated by an LMSR market maker:

```text
price_i = exp(q_i / b) / sum(exp(q_j / b))
```

`q_i` is `MarketOutcome.net_shares` (net shares the market maker has sold) and
`b` is `Market.liquidity_b` (default `1000`, larger means deeper liquidity). The
result is quantized to integer ticks that always sum to `CREDIT_SCALE`, so the
displayed prices for all active outcomes sum to 100%. Prices move automatically
with trading volume; there is no last-trade price per outcome.

The platform liquidity account is the sole counterparty. Orders execute
immediately against the market maker and never rest in an order book:

- A BUY mints complete sets when the market maker does not already hold the
  requested outcome, sells the requested shares, and keeps the complementary
  shares. Each minted set reserves `CREDIT_SCALE` credits of collateral in the
  liquidity wallet, so every winning payout is fully funded.
- A SELL buys shares back from the user at the LMSR refund. No shares are minted
  or burned; the market maker's `net_shares` for that outcome decrease.
- Existing market-maker inventory is sold before new sets are minted, and
  `Market.issuance_limit` caps sets per market.

`limit_price` is a slippage cap, not a resting price: it is the maximum average
price for a BUY and the minimum average price for a SELL. The largest quantity
that respects the cap is filled; any unfilled remainder is cancelled
immediately. Omit `limit_price` for a market order (BUY defaults to `9999`,
SELL to `1`). BUY orders debit the exact LMSR notional (rounded up); SELL orders
credit the exact refund (rounded down), which prevents rounding arbitrage.
SELL orders can only sell shares the user owns, so naked short selling is not
supported.

On resolution, the reserved issuance collateral funds the winning payouts. On a
void market it funds cost-basis refunds. Because complete sets pay exactly
`CREDIT_SCALE` when they win, resolved payouts never create credits; any void
refund above the reserved collateral is platform-funded, as before.

Wallet balances are mutated only through wallet services and ledger entries.
Ledger rows are immutable audit records with unique idempotency keys. Critical
trading operations use `transaction.atomic()` and row locks around orders,
wallets, and positions.

## Market Maker Pricing

`apps.trading.pricing` contains the pure pricing math: stable LMSR
probabilities, integer quantization, `buy_cost`, `sell_refund`, and bounded
searches for the largest fillable quantity. `apps.trading.services` applies the
same math for execution, quotes, order-book depth, and settlement. Because both
paths share the pricing module, an API quote matches the eventual execution
exactly for the same state.

The legacy `apps.trading.matching.order_book.OrderBook` remains as a
deterministic heap/FIFO helper for snapshots and tests, but it is not used to
execute trades. PostgreSQL is the authoritative source of truth for prices,
positions, and cash.

## APIs

Authentication routes remain under `/api/auth/`.

Market discovery:

- `GET /api/categories/`
- `GET /api/markets/`
- `GET /api/markets/{slug}/`
- `GET /api/markets/{slug}/stats/`
- `GET /api/outcomes/{id}/history/?range=1D`

Trading and portfolio:

- `POST /api/trading/orders/`
- `GET /api/trading/orders/`
- `DELETE /api/trading/orders/{id}/`
- `GET /api/trading/quote/?outcome={id}&side=buy&quantity={n}`
- `GET /api/trading/trades/?outcome={id}`
- `GET /api/trading/positions/`
- `GET /api/trading/positions/summary/`
- `GET /api/trading/order-books/{outcome_id}/`
- `GET /api/trading/activity/?outcome={id}`

Wallet:

- `GET /api/wallet/`
- `GET /api/wallet/ledger/`

Notifications:

- `GET /api/notifications/` (supports `?unread=true` and `?event_type=order_filled`)
- `GET /api/notifications/unread-count/`
- `POST /api/notifications/{id}/read/`
- `POST /api/notifications/mark-all-read/`
- `POST /api/notifications/devices/`
- `GET /api/notifications/devices/`
- `DELETE /api/notifications/devices/{id}/`

In-app notifications are persisted for order fills and partial fills, order
cancellations, market close, and position settlement. Each notification is
pushed to the user's active registered devices through Firebase Cloud
Messaging after the surrounding database transaction commits. Push delivery is
best effort: missing Firebase credentials or device tokens do not fail the
underlying trading operation, and unregistered tokens are deactivated
automatically.

Market administration:

- `POST /api/markets/{slug}/publish/`
- `POST /api/markets/{slug}/close/`
- `POST /api/markets/{slug}/resolve/`
- `POST /api/markets/{slug}/upload_image/`

## Chart History

Chart points are generated only from committed trades. Each trade updates the
one-minute bucket for every active outcome of its market with the resulting
unified price vector and the trade's interval volume. The history endpoint
returns Unix timestamps, decimal prices, volume, and seconds until the market
resolution deadline.

## Operations

Run reconciliation before and after high-risk changes:

```powershell
python manage.py reconcile_marketx
```

It reports wallet-ledger mismatches, order-fill mismatches, reserved position
discrepancies, and market-maker invariants: total position quantity per outcome
must equal `Market.issued_quantity`, `net_shares` cannot exceed issued shares,
and reserved house collateral must equal `issued_quantity * CREDIT_SCALE`. It
does not mutate data.

## Tests

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings_test_sqlite"
python manage.py test
```

`config.settings_test_sqlite` runs the suite against an isolated in-memory
SQLite database, so the configured PostgreSQL database is left untouched.

## Environment Variables

See `.env.example` for placeholders. Keep real `.env` files and Firebase
credentials out of git. `GOOGLE_OAUTH_CLIENT_ID`, Firebase credentials, and
Cloudinary credentials are optional for tests and local API startup unless the
corresponding integration is exercised.

Firebase push delivery reads `FIREBASE_CREDENTIALS_PATH` or
`FIREBASE_CREDENTIALS_JSON`. If neither is configured, it also looks for
`firebase-credentials.json` at the project root or in `config/`. When no
credentials or device tokens are available, notifications are still stored
in-app and push delivery is skipped without failing the request.

The platform liquidity account used by the market maker is configured with
`MARKETX_HOUSE_EMAIL`, `MARKETX_HOUSE_USERNAME`, and `MARKETX_HOUSE_CREDITS`.
The credits value is allocated once when the liquidity wallet is first created.
Each market also has `Market.liquidity_b` (default `1000`), which controls price
depth; larger values make prices move more slowly for the same volume.

## Current Real-time Strategy

Trading writes durable `ActivityEvent` rows for public market events and private
user events after authoritative database changes. These events are available via
the activity API and are suitable for later WebSocket fan-out. This repository
does not yet enable Django Channels because the dependency is not present in the
current environment; do not treat WebSocket delivery as a source of truth.

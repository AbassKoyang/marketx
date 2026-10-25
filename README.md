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
units equals one virtual payout credit. Limit prices are ticks from `1` to
`9999`; a contract pays `10000` units if its outcome wins. Quantities are whole
contracts.

BUY orders reserve `limit_price * quantity` virtual-credit units in the wallet.
Trades execute at the resting order's limit price. If a BUY executes below its
limit, the excess reservation for that fill is released immediately. SELL
orders reserve existing position quantity, so naked short selling is not
supported.

Markets do not need a pre-existing seller. When a BUY cannot be filled by a
resting SELL, the platform liquidity account mints a complete set: one share of
every active outcome. The buyer receives the requested outcome at their limit
price and the liquidity account keeps the complementary shares. Each minted set
reserves `CREDIT_SCALE` credits of collateral in the liquidity wallet, so every
winning payout is fully funded. Existing liquidity inventory is sold before new
sets are minted, and `Market.issuance_limit` caps sets per market.

On resolution, the reserved issuance collateral funds the winning payouts. On a
void market it funds cost-basis refunds. Because complete sets pay exactly
`CREDIT_SCALE` when they win, resolved payouts never create credits; any void
refund above the reserved collateral is platform-funded, as before.

Wallet balances are mutated only through wallet services and ledger entries.
Ledger rows are immutable audit records with unique idempotency keys. Critical
trading operations use `transaction.atomic()` and row locks around orders,
wallets, and positions.

## Matching Rules

PostgreSQL is the authoritative source of truth. Open orders are matched with
price-time priority:

- incoming buys match the lowest priced resting sells at or below the buy limit;
- incoming sells match the highest priced resting buys at or above the sell
  limit;
- orders at the same price are matched by creation time and primary key;
- a BUY that still has quantity left is filled by primary issuance, so the
  first trade in a market can happen without any seller;
- partial fills update remaining quantities and keep the unfilled order open;
- cancellations release unfilled BUY reservations or SELL reserved quantity.

`apps.trading.matching.order_book.OrderBook` provides a deterministic heap plus
FIFO price-level implementation for tests and snapshots. It handles stale heap
entries lazily; the database remains authoritative.

## APIs

Authentication routes remain under `/api/auth/`.

For a frontend-agent-ready admin dashboard contract, including endpoint
payloads, TanStack Query guidance, and shadcn/ui design direction, see
[`docs/admin-dashboard-api-guide.md`](docs/admin-dashboard-api-guide.md).

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
- `GET /api/trading/trades/?outcome={id}`
- `GET /api/trading/positions/`
- `GET /api/trading/positions/summary/`
- `GET /api/trading/order-books/{outcome_id}/`
- `GET /api/trading/activity/?outcome={id}`

Wallet:

- `GET /api/wallet/`
- `GET /api/wallet/ledger/`

Notifications:

- `POST /api/notifications/devices/`
- `GET /api/notifications/devices/`
- `DELETE /api/notifications/devices/{id}/`

Market administration:

- `POST /api/markets/{slug}/publish/`
- `POST /api/markets/{slug}/close/`
- `POST /api/markets/{slug}/resolve/`
- `POST /api/markets/{slug}/upload_image/`

## Chart History

Chart points are generated only from committed trades. Each trade updates the
one-minute bucket for its outcome with the latest traded price and interval
volume. The history endpoint returns Unix timestamps, decimal prices, volume,
and seconds until the market resolution deadline.

## Operations

Run reconciliation before and after high-risk changes:

```powershell
python manage.py reconcile_marketx
```

It reports wallet-ledger mismatches, order-fill mismatches, and reserved
position discrepancies. It does not mutate data.

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

The platform liquidity account used for primary issuance is configured with
`MARKETX_HOUSE_EMAIL`, `MARKETX_HOUSE_USERNAME`, and `MARKETX_HOUSE_CREDITS`.
The credits value is allocated once when the liquidity wallet is first created.

## Current Real-time Strategy

Trading writes durable `ActivityEvent` rows for public market events and private
user events after authoritative database changes. These events are available via
the activity API and are suitable for later WebSocket fan-out. This repository
does not yet enable Django Channels because the dependency is not present in the
current environment; do not treat WebSocket delivery as a source of truth.

# Backend Change Report: LMSR Market Maker

**Commit:** `361d2a5 feat: add LMSR market maker pricing and buybacks`  
**Audience:** AI coding agents updating `marketx-frontend` and `marketx-admin`  
**Goal:** Update both clients without runtime errors, incorrect prices, or wrong accounting.

---

## 1. Executive Summary

The backend no longer uses a user-to-user order book or primary issuance at the
buyer's limit price. Every market now has an **LMSR automated market maker
(AMM)**:

- The platform liquidity account is the **sole counterparty** for every trade.
- Prices are calculated from net shares traded and **always sum to 100%**.
- A BUY can happen with no seller because the AMM mints complete sets.
- A SELL buys shares back from the AMM. Users still cannot sell shares they do
  not own.
- Orders execute **immediately**. Nothing rests in an order book.
- `limit_price` is now an optional **slippage cap**, not an execution price.
- A partially fillable order fills what it can and **cancels the remainder
  immediately**.
- Wallet cash moves immediately by the exact LMSR `notional`.

The old fields mostly still exist, but several have **changed meaning**. Read
section 3 before touching any price display.

---

## 2. Breaking Changes

| Area | Before | Now | Required client action |
|---|---|---|---|
| Market price | Per-outcome last trade price | One LMSR vector, ticks sum to `10000` | Use `current_prices`, `outcomes[].current_price`, or `stats.prices` |
| `outcomes[].latest_price` (market detail) | Last trade price | Alias for `current_price` (LMSR) | Do not treat as last trade |
| `stats.latest_price` | Last trade price | Still the last trade's average price, **not** current probability | Use `stats.prices` for current prices |
| Order submission | `limit_price` required; order could rest | `limit_price` optional slippage cap; order always executes or rejects | Make limit optional; never poll for a resting fill |
| Order status `partially_filled` | Remainder stayed open | Remainder is cancelled; `cancelled_at` is set | Treat as terminal, not active |
| BUY cash flow | Reserved `limit_price * quantity` | Debits exact `notional` immediately | Read wallet after response; do not reserve locally |
| Trade cash | `price * quantity` | Exact `notional` may differ by rounding | Use `trade.notional`, not `price * quantity` |
| Position `last_price` | Last trade price | Alias for `current_price` (LMSR) | Use `current_price` / `market_value` |
| Order book | Real resting user orders | Synthetic AMM depth | Label as indicative liquidity, not user orders |
| Resolved prices | Last trade / resolution points | Winner `10000`, losers `0` | Render `100%` / `0%` after resolution |
| Admin lists | Only real users | House AMM account and `amm:*` orders appear | Filter or label market-maker records |

---

## 3. Pricing Semantics

### 3.1 Units

- `CREDIT_SCALE = 10000`
- `MIN_PRICE = 1`, `MAX_PRICE = 9999` for open-market LMSR ticks.
- A winning contract pays `10000` units.
- Prices are integer ticks. `5000` = 50%, `10000` = 100%, `0` = 0%.
- Quantities are whole contracts (shares).
- All cash values are integer credit units. Divide by `10000` for display.

### 3.2 The price vector

For active outcomes of a market:

```text
price_i = exp(q_i / b) / sum(exp(q_j / b))
```

- `q_i` is `MarketOutcome.net_shares` (net shares the AMM has sold).
- `b` is `Market.liquidity_b` (default `1000`).
- Quantized ticks always sum to `10000` while the market is open.
- A resolved market returns winner `10000`, losers `0` (still sums to `10000`).
- A void market returns the LMSR vector frozen at close (no winner).
- Inactive outcomes are excluded and their `current_price` is `null`.

**Never sum per-outcome last trade prices.** Use the vector returned by the
backend.

### 3.3 Price field map

| Endpoint | Field | Type | Meaning |
|---|---|---|---|
| `GET /api/markets/{slug}/` | `outcomes[].current_price` | tick or `null` | Current LMSR tick |
| `GET /api/markets/{slug}/` | `outcomes[].current_price_decimal` | `0..1` or `null` | Current price as decimal |
| `GET /api/markets/{slug}/` | `current_prices[]` | array | Current vector with `outcome_id`, `name`, `slug`, `price`, `price_decimal` |
| `GET /api/markets/{slug}/stats/` | `prices` | object keyed by **outcome slug** | Current vector as decimals, sums to `1.0` |
| `GET /api/markets/{slug}/stats/` | `depth[slug].current_price` | tick | Current LMSR tick |
| `GET /api/trading/quote/` | `current_price` | tick | Marginal price before the quote |
| `GET /api/trading/quote/` | `prices_after` | object keyed by **string outcome ID** | Post-fill vector, ticks |
| `GET /api/trading/quote/` | `prices_after_decimal` | object keyed by **string outcome ID** | Post-fill vector, decimals |
| `GET /api/trading/positions/` | `current_price` | tick | Current LMSR tick for the position outcome |
| `GET /api/outcomes/{id}/history/` | `data[].price` | decimal | Historical LMSR price |

### 3.4 `latest_price` traps

There are three different `latest_price`/`last_price` fields:

1. `GET /api/markets/{slug}/` → `outcomes[].latest_price` is an **alias for
   `current_price`**, not the last trade.
2. `GET /api/markets/{slug}/stats/` → `latest_price` is the **last trade's
   average price** as a decimal. It can be stale and is not the probability.
3. `GET /api/trading/positions/` → `last_price` is an **alias for
   `current_price`**, not the last trade.

**Recommendation:** stop using `latest_price` and `last_price` for price
display. Use `current_price` / `current_prices` / `prices`.

---

## 4. Data Model Changes

### 4.1 `Market`

New/changed fields exposed by the API:

| Field | Type | Notes |
|---|---|---|
| `liquidity_b` | positive integer | LMSR depth. Writable by staff on create/update, min `1`. Default `1000`. |
| `issuance_limit` | positive integer | Read-only in the API. Caps minted complete sets. |
| `issued_quantity` | positive integer | Read-only. Number of complete sets minted so far. |
| `current_prices` | array | New. Current price vector. |

Changing `liquidity_b` after trading **immediately reprices all outcomes** for
the same net shares. Do not expose it casually in the admin UI.

### 4.2 `MarketOutcome`

| Field | Type | Notes |
|---|---|---|
| `net_shares` | positive integer | Read-only. Net shares the AMM has sold for this outcome. |
| `current_price` | integer or `null` | New. Current LMSR tick. `null` if inactive. |
| `current_price_decimal` | number or `null` | New. |
| `latest_price` | integer or `null` | Legacy alias for `current_price`. |

`is_active` cannot be changed after `issued_quantity > 0`. Attempting it returns
`400 {"non_field_errors": ["Outcomes cannot be activated or deactivated after share issuance has started."]}`.

### 4.3 `Trade`

| Field | Type | Notes |
|---|---|---|
| `notional` | integer | New. Exact LMSR cash amount in credit units. |
| `notional_decimal` | number | New. `notional / 10000`. |
| `price` | tick | Average execution tick (rounded in the trader's favour). |
| `price_decimal` | number | `price / 10000`. |

For historical rows created before this migration, `notional` may be `0`. When
`notional == 0`, fall back to `price * quantity` for display only.

### 4.4 `Order`

No field was removed. Semantics changed:

- `limit_price` is a slippage cap, not a resting price.
- A market BUY is stored with `limit_price = 9999`.
- A market SELL is stored with `limit_price = 1`.
- New orders never have status `open`.
- `partially_filled` means **partially filled and remainder cancelled**.
  `cancelled_at` is non-null.
- `remaining_quantity` is the cancelled remainder for new partial fills.
- `active=true` returns only legacy open/partial orders with
  `cancelled_at = null`.

### 4.5 `Position`

| Field | Type | Notes |
|---|---|---|
| `current_price` | integer or `null` | New. Current LMSR tick. |
| `current_price_decimal` | number or `null` | New. |
| `market_value` | integer or `null` | New. `quantity * current_price`. |
| `unrealized_pnl` | integer or `null` | New. `market_value - cost_basis`. |
| `last_price` | integer or `null` | Legacy alias for `current_price`. |
| `average_entry_price` | number or `null` | Decimal, `(cost_basis / quantity) / 10000`. |

Settled positions have `quantity = 0`, `cost_basis = 0`, and
`market_value = 0`; `realized_pnl` includes the settlement payout.

---

## 5. API Contract Changes

### 5.1 `GET /api/markets/{slug}/`

Auth: public. Not paginated.

New/changed response fields:

```json
{
  "id": 3,
  "slug": "will-it-rain",
  "status": "open",
  "liquidity_b": 1000,
  "issuance_limit": 10000,
  "issued_quantity": 10,
  "outcomes": [
    {
      "id": 12,
      "market": 3,
      "name": "Yes",
      "slug": "yes",
      "display_order": 1,
      "is_active": true,
      "net_shares": 10,
      "latest_price": 5025,
      "current_price": 5025,
      "current_price_decimal": 0.5025
    },
    {
      "id": 13,
      "market": 3,
      "name": "No",
      "slug": "no",
      "display_order": 2,
      "is_active": true,
      "net_shares": 0,
      "latest_price": 4975,
      "current_price": 4975,
      "current_price_decimal": 0.4975
    }
  ],
  "current_prices": [
    {
      "outcome_id": 12,
      "name": "Yes",
      "slug": "yes",
      "price": 5025,
      "price_decimal": 0.5025
    },
    {
      "outcome_id": 13,
      "name": "No",
      "slug": "no",
      "price": 4975,
      "price_decimal": 0.4975
    }
  ],
  "resolution": null
}
```

Rules:

- `current_prices` includes only active outcomes, in `display_order, id` order.
- `sum(outcome.current_price)` over active outcomes is exactly `10000`.
- `sum(current_prices[].price_decimal)` is `1.0`.
- On a resolved market: winner `10000` / `1.0`, losers `0` / `0.0`.
- On a void market: LMSR vector frozen at close.
- `latest_price` on each outcome is the same as `current_price`; do not use it
  for trade-tape or chart data.

### 5.2 `GET /api/markets/{slug}/stats/`

Auth: public. Not paginated.

```json
{
  "latest_price": 0.5012,
  "prices": {
    "yes": 0.5025,
    "no": 0.4975
  },
  "liquidity_b": 1000,
  "total_volume": 10,
  "trade_count": 1,
  "first_trade_at": "2026-10-25T12:00:00Z",
  "last_trade_at": "2026-10-25T12:00:00Z",
  "depth": {
    "yes": {
      "current_price": 5025,
      "current_price_decimal": 0.5025,
      "bids": [
        { "price": 5023, "quantity": 1 },
        { "price": 5020, "quantity": 2 }
      ],
      "asks": [
        { "price": 5027, "quantity": 1 },
        { "price": 5030, "quantity": 2 }
      ]
    },
    "no": {
      "current_price": 4975,
      "current_price_decimal": 0.4975,
      "bids": [],
      "asks": [
        { "price": 4977, "quantity": 1 },
        { "price": 4980, "quantity": 2 }
      ]
    }
  }
}
```

Notes:

- `prices` is keyed by **outcome slug**, not ID.
- `prices` sums to `1.0` for active outcomes, including resolved markets.
- `latest_price` is the last trade's average price (decimal), not the current
  probability. It is `null` before the first trade.
- `total_volume` is the sum of trade **quantities** (shares), not credits.
- `depth` is synthetic AMM liquidity, not resting user orders.
- `depth[slug].current_price` is a tick; `current_price_decimal` is a decimal.
- The `depth` example above is truncated. A live response returns up to
  `limit` levels per outcome (`limit=5` for this endpoint), with geometrically
  increasing quantities (`1, 2, 4, 8, ...`).

### 5.3 `GET /api/outcomes/{id}/history/`

Auth: public. Not paginated (custom `{data, pagination}` envelope).

Shape is unchanged, but semantics changed:

- Every trade writes a price point for **every active outcome** of the market.
- `price` is the unified LMSR price as a decimal.
- `volume` is populated for the traded outcome with its quantity in the minute
  bucket. Other outcomes in the same vector may carry `0` or an earlier
  bucket's value; do not use non-traded outcomes for volume totals.
- Resolution writes a final point: winner `1.0`, losers `0.0`.
- Void markets do not write a resolution point.

```json
{
  "data": [
    {
      "timestamp": 1792929600,
      "price": 0.5025,
      "resolution_seconds": 43200,
      "volume": 10
    }
  ],
  "pagination": {
    "limit": 1000,
    "offset": 0,
    "has_more": false,
    "next_cursor": null
  }
}
```

### 5.4 `POST /api/trading/orders/`

Auth: authenticated user.

Request:

```json
{
  "outcome": 12,
  "side": "buy",
  "limit_price": 5200,
  "quantity": 10,
  "client_order_id": "ui-uuid-v4"
}
```

- `limit_price` is **optional**.
- Omit it (or send `null`) for a market order:
  - BUY defaults to `9999` (accept any price).
  - SELL defaults to `1` (accept any price).
- For BUY, `limit_price` is the maximum **average** price per share.
- For SELL, `limit_price` is the minimum **average** price per share.
- `quantity` is a positive integer.
- `client_order_id` is required, max 100 chars, unique per user.

Response: `201` with the `Order`.

```json
{
  "id": 1,
  "outcome": 12,
  "outcome_name": "Yes",
  "market_id": 3,
  "market_title": "Will it rain?",
  "market_slug": "will-it-rain",
  "side": "buy",
  "limit_price": 5200,
  "original_quantity": 10,
  "filled_quantity": 10,
  "remaining_quantity": 0,
  "status": "filled",
  "client_order_id": "ui-uuid-v4",
  "cancelled_at": null,
  "created_at": "2026-10-25T12:00:00Z",
  "updated_at": "2026-10-25T12:00:00Z"
}
```

Execution rules:

- Full fill: `status = "filled"`, `cancelled_at = null`.
- Partial fill: `status = "partially_filled"`, `cancelled_at` is set, and
  `remaining_quantity` is the cancelled remainder.
- Nothing fillable: `400 {"detail": ["No quantity can fill at this limit."]}`.
  No order is created. The message is generic: the cause may be the slippage
  cap, insufficient balance, insufficient position, or AMM liquidity.
- Closed market:
  `400 {"detail": ["Orders can only be placed on open markets."]}`.
- Outside trading window:
  `400 {"detail": ["Market is not inside its trading window."]}`.
- Idempotency:
  - Same user + same `client_order_id` + same payload → `201` with the original
    order. It is **not** a new trade.
  - Same `client_order_id` + different payload →
    `400 {"detail": ["client_order_id was already used for a different order."]}`.
- New orders never rest. Do not poll for later fills.
- A market order's `limit_price` is `9999` (BUY) or `1` (SELL). Do not display
  it as the execution price. Use the trade `notional` or quote
  `average_price`.

### 5.5 `GET /api/trading/quote/` (new)

Auth: authenticated user. Read-only, no state mutation.

```http
GET /api/trading/quote/?outcome=12&side=buy&quantity=10
```

Query params:

- `outcome` (required): outcome ID.
- `side` (required): `buy` or `sell`.
- `quantity` (required): positive integer.
- `limit_price` (optional): `1..9999`; defaults as in order submission.

Example response for a fresh binary market (`5000/5000`, `b=1000`):

```json
{
  "outcome": 12,
  "outcome_name": "Yes",
  "market_id": 3,
  "market_slug": "will-it-rain",
  "side": "buy",
  "requested_quantity": 10,
  "fillable_quantity": 10,
  "notional": 50125,
  "notional_decimal": 5.0125,
  "average_price": 5012,
  "average_price_decimal": 0.5012,
  "current_price": 5000,
  "current_price_decimal": 0.5,
  "prices_after": {
    "12": 5025,
    "13": 4975
  },
  "prices_after_decimal": {
    "12": 0.5025,
    "13": 0.4975
  },
  "limited_by": null
}
```

- `fillable_quantity` may be less than `requested_quantity`.
- `limited_by` is one of:
  - `null` — full quantity can fill.
  - `"limit"` — slippage cap prevents more.
  - `"balance"` — buyer has insufficient available credits.
  - `"position"` — seller does not own enough shares.
  - `"liquidity"` — AMM inventory, issuance, house collateral, or AMM net
    shares limit.
- A valid quote that cannot fill returns `200` with
  `fillable_quantity: 0`, not an error.
- A SELL cannot exceed the AMM's `net_shares` for that outcome, even if the
  user owns more shares. In that case `limited_by` is `"liquidity"`.
- `average_price` is `null` when `fillable_quantity` is `0`.
- `prices_after` is keyed by **string outcome ID**.
- The quote matches eventual execution for the same state. Call it before
  submit to show expected cash and slippage.
- Invalid input, unknown outcome, or a non-open market returns `400`.

### 5.6 `GET /api/trading/trades/`

Auth: public. Paginated.

```json
{
  "id": 1,
  "execution_id": "4dffbb62-d604-4d5d-83db-9898b5f40c2c",
  "outcome": 12,
  "outcome_name": "Yes",
  "market_id": 3,
  "market_title": "Will it rain?",
  "market_slug": "will-it-rain",
  "price": 5012,
  "price_decimal": 0.5012,
  "quantity": 10,
  "notional": 50125,
  "notional_decimal": 5.0125,
  "created_at": "2026-10-25T12:00:00Z"
}
```

- `price` is the average execution tick, rounded in the trader's favour.
- `notional` is the exact cash amount. It may not equal
  `price * quantity`.
- Public trades omit buyer/seller IDs.
- Query params: `page`, `outcome`, `mine=true|1`.
- `mine=true` returns trades where the current user is buyer or seller.

### 5.7 `GET /api/trading/positions/`

Auth: authenticated user. Paginated.

```json
{
  "id": 1,
  "outcome": 12,
  "outcome_name": "Yes",
  "market_id": 3,
  "market_title": "Will it rain?",
  "market_slug": "will-it-rain",
  "market_status": "open",
  "market_closes_at": "2026-10-26T12:00:00Z",
  "quantity": 10,
  "reserved_quantity": 0,
  "cost_basis": 50125,
  "realized_pnl": 0,
  "average_entry_price": 0.50125,
  "last_price": 5025,
  "current_price": 5025,
  "current_price_decimal": 0.5025,
  "market_value": 50250,
  "unrealized_pnl": 125,
  "settled_at": null,
  "updated_at": "2026-10-25T12:00:00Z"
}
```

- `current_price`, `market_value`, `unrealized_pnl` are the fields to use for
  portfolio valuation.
- `last_price` is an alias for `current_price`; do not use it for a trade tape.
- `average_entry_price` is a decimal, not a tick.
- `market_value` and `unrealized_pnl` are integer credit units.
- `reserved_quantity` is normally `0`; it only exists for legacy resting SELL
  orders.
- Settled positions remain in the list with `quantity: 0`, `cost_basis: 0`,
  `market_value: 0`, `unrealized_pnl: 0`.

### 5.8 `GET /api/trading/positions/summary/`

Auth: authenticated user. Not paginated.

```json
{
  "open_positions": 1,
  "total_cost_basis": 50125,
  "realized_pnl": 0
}
```

`total_cost_basis` and `realized_pnl` are integer credit units.

### 5.9 `GET /api/trading/order-books/{outcome_id}/`

Auth: public. Not paginated.

```json
{
  "current_price": 5025,
  "current_price_decimal": 0.5025,
  "bids": [
    { "price": 5023, "quantity": 1 },
    { "price": 5020, "quantity": 2 }
  ],
  "asks": [
    { "price": 5027, "quantity": 1 },
    { "price": 5030, "quantity": 2 }
  ]
}
```

- This is **synthetic AMM depth**, not resting user orders.
- `bids` are AMM buy-back levels; `asks` are AMM sell levels.
- `price` is a tick; convert to decimal for display.
- The example above is truncated. Levels use geometrically increasing
  quantities (`1, 2, 4, 8, ...`) and the response returns up to `limit`
  levels per side.
- Query param `limit`: default `20`, max `100`, must be positive.
- Closed/resolved markets return empty `bids`/`asks`, with
  `current_price` still populated (resolution-aware).
- Unknown outcome returns `404 {"detail": "Outcome not found."}`.
- Invalid `limit` returns `400 {"detail": "limit must be an integer."}` or
  `{"detail": "limit must be positive."}`.

### 5.10 `GET /api/trading/activity/`

Auth: public (public events) / authenticated (private events). Paginated.

Relevant event payloads:

```json
{
  "event_type": "trade_executed",
  "visibility": "public",
  "payload": {
    "trade_id": 1,
    "price": 5012,
    "quantity": 10,
    "notional": 50125,
    "market_id": 3,
    "prices": { "12": 5025, "13": 4975 }
  }
}
```

```json
{
  "event_type": "order_filled",
  "visibility": "private",
  "payload": {
    "trade_id": 1,
    "order_id": 1,
    "side": "buy",
    "quantity": 10,
    "price": 5012,
    "notional": 50125
  }
}
```

```json
{
  "event_type": "order_cancelled",
  "visibility": "private",
  "payload": {
    "order_id": 1,
    "released_quantity": 5
  }
}
```

```json
{
  "event_type": "position_settled",
  "visibility": "private",
  "payload": {
    "position_id": 1,
    "payout": 100000,
    "market_id": 3
  }
}
```

- `payload.prices` is the **post-trade** vector, keyed by **string outcome ID**,
  and contains ticks.
- `order_cancelled` fires only for an explicit cancel of a legacy resting
  order. A new order's partially filled remainder is cancelled without
  emitting `order_cancelled`.
- `trade_executed` includes `notional`.

### 5.11 Wallet and ledger

`GET /api/wallet/` shape is unchanged:

```json
{
  "available_balance": 49875,
  "reserved_balance": 0,
  "created_at": "...",
  "updated_at": "..."
}
```

`GET /api/wallet/ledger/` is paginated. New trade entry types:

- `available_debit` — cash spent on a BUY.
- `available_credit` — cash received from a SELL or settlement.

Legacy `order_reserve` / `order_release` entries may still exist for old
orders. New orders do not create them.

Example trade entry:

```json
{
  "id": 1,
  "entry_type": "available_debit",
  "available_delta": -50125,
  "reserved_delta": 0,
  "reference_type": "trade",
  "reference_id": 1,
  "description": "Credits spent buying outcome shares",
  "created_at": "2026-10-25T12:00:00Z"
}
```

The house wallet's `reserved_balance` is issuance collateral, not customer
funds.

---

## 6. Updated TypeScript Types

Use these as the source of truth.

```ts
export const CREDIT_SCALE = 10_000
export const MIN_PRICE = 1
export const MAX_PRICE = 9_999

export type MarketStatus = "draft" | "open" | "closed" | "resolved" | "void"
export type MarketType = "binary" | "multi_outcome"
export type OrderSide = "buy" | "sell"
export type OrderStatus = "open" | "partially_filled" | "filled" | "cancelled"

export type Paginated<T> = {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

export type MarketCategory = {
  id: number
  name: string
  slug: string
  description: string
  icon: string
  is_active: boolean
  display_order: number
}

export type MarketPrice = {
  outcome_id: number
  name: string
  slug: string
  /** Integer tick. 0..10000. */
  price: number
  /** Decimal probability. 0..1. */
  price_decimal: number
}

export type MarketOutcome = {
  id: number
  market: number
  name: string
  slug: string
  description: string
  image_url: string
  display_order: number
  is_active: boolean
  net_shares: number
  /** @deprecated Alias for current_price on market detail. */
  latest_price: number | null
  current_price: number | null
  current_price_decimal: number | null
}

export type MarketResolution = {
  winning_outcome: number | null
  is_void: boolean
  evidence_summary: string
  evidence_url: string
  resolved_at: string
}

export type Market = {
  id: number
  title: string
  slug: string
  market_type: MarketType
  question: string
  description: string
  category: MarketCategory
  /** Write-only on create/update; responses use `category`. */
  category_id?: number
  resolution_criteria: string
  resolution_source_description: string
  resolution_source_url: string
  image_url: string
  image_public_id: string
  status: MarketStatus
  opens_at: string
  closes_at: string
  resolution_deadline: string
  is_featured: boolean
  issuance_limit: number
  issued_quantity: number
  liquidity_b: number
  outcomes: MarketOutcome[]
  current_prices: MarketPrice[]
  resolution: MarketResolution | null
  created_at: string
  updated_at: string
}

export type Trade = {
  id: number
  execution_id: string
  outcome: number
  outcome_name: string
  market_id: number
  market_title: string
  market_slug: string
  /** Average execution tick. */
  price: number
  price_decimal: number
  quantity: number
  /** Exact cash amount in credit units. */
  notional: number
  notional_decimal: number
  created_at: string
}

export type Order = {
  id: number
  outcome: number
  outcome_name: string
  market_id: number
  market_title: string
  market_slug: string
  side: OrderSide
  /** Slippage cap, not execution price. 9999 for market BUY, 1 for market SELL. */
  limit_price: number
  original_quantity: number
  filled_quantity: number
  /** Cancelled remainder for new partial fills. */
  remaining_quantity: number
  status: OrderStatus
  client_order_id: string
  cancelled_at: string | null
  created_at: string
  updated_at: string
}

export type Position = {
  id: number
  outcome: number
  outcome_name: string
  market_id: number
  market_title: string
  market_slug: string
  market_status: MarketStatus
  market_closes_at: string
  quantity: number
  reserved_quantity: number
  cost_basis: number
  realized_pnl: number
  /** Decimal average entry price. */
  average_entry_price: number | null
  /** @deprecated Alias for current_price. */
  last_price: number | null
  current_price: number | null
  current_price_decimal: number | null
  market_value: number | null
  unrealized_pnl: number | null
  settled_at: string | null
  updated_at: string
}

export type Quote = {
  outcome: number
  outcome_name: string
  market_id: number
  market_slug: string
  side: OrderSide
  requested_quantity: number
  fillable_quantity: number
  notional: number
  notional_decimal: number
  average_price: number | null
  average_price_decimal: number | null
  current_price: number
  current_price_decimal: number
  /** Keyed by string outcome ID. */
  prices_after: Record<string, number>
  prices_after_decimal: Record<string, number>
  limited_by: "limit" | "balance" | "position" | "liquidity" | null
}

export type DepthLevel = {
  /** Integer tick. */
  price: number
  quantity: number
}

export type OrderBook = {
  current_price: number | null
  current_price_decimal: number | null
  bids: DepthLevel[]
  asks: DepthLevel[]
}

export type MarketStats = {
  /** @deprecated Last trade average price, not current probability. */
  latest_price: number | null
  /** Keyed by outcome slug. Sums to 1.0. */
  prices: Record<string, number>
  liquidity_b: number
  total_volume: number
  trade_count: number
  first_trade_at: string | null
  last_trade_at: string | null
  depth: Record<string, OrderBook>
}
```

---

## 7. Frontend (`marketx-frontend`) Update Checklist

### 7.1 Price display

- [ ] Replace any last-trade-price aggregation with the backend price vector.
- [ ] Use `market.current_prices` or `market.outcomes[].current_price` for
      market cards and detail headers.
- [ ] Use `stats.prices[outcome.slug]` for stats widgets.
- [ ] Render `price_decimal * 100` as a percentage with one decimal place.
- [ ] Support `binary` and `multi_outcome`; do not hardcode two outcomes.
- [ ] Do not assume `current_price` is always `1..9999`:
      resolved winners are `10000`, losers are `0`, and a single active outcome
      is `10000`.
- [ ] Handle inactive outcomes with `current_price === null`.
- [ ] Do not use `latest_price` / `last_price` for current probability.
- [ ] Do not derive a price vector by normalizing per-outcome last trades.

### 7.2 Trade ticket

- [ ] Make `limit_price` optional in the form. Label it "slippage cap" or
      "limit price".
- [ ] Show a market-order option that omits `limit_price`.
- [ ] Debounce a call to `GET /api/trading/quote/` when side/quantity/limit
      change.
- [ ] Show `fillable_quantity`, `notional_decimal`, `average_price_decimal`,
      and `limited_by`.
- [ ] If `fillable_quantity < requested_quantity`, warn that only that amount
      can fill and the remainder will be cancelled.
- [ ] Submit the order once. Do not poll for a resting fill.
- [ ] On `201`, use the returned `Order` and refetch wallet/positions/trades.
- [ ] On `400`, show `detail` (which may be an array) or field errors.
- [ ] Do not display a market order's `limit_price` (`9999` / `1`) as the
      execution price.
- [ ] Do not compute cash as `limit_price * quantity` or `price * quantity`;
      use `trade.notional` / `quote.notional`.

### 7.3 Order list and cancel

- [ ] Treat `partially_filled` with `cancelled_at != null` as terminal.
- [ ] Only show a cancel button when `status` is `open` or
      `partially_filled` **and** `cancelled_at == null`.
- [ ] Expect new orders to be `filled` or `partially_filled` immediately.
- [ ] Do not use `PUT`/`PATCH` on orders; the API only supports list, create,
      and `DELETE` (cancel).
- [ ] Handle `404 {"detail": "Order not found."}` on cancel.
- [ ] Do not assume `DELETE` changes status; a filled order is returned
      unchanged.

### 7.4 Portfolio

- [ ] Use `current_price`, `market_value`, and `unrealized_pnl` from the
      position payload.
- [ ] Do not recompute valuation from `last_price` or `cost_basis`.
- [ ] Show settled positions as closed; they remain in the list with zero
      quantity.
- [ ] `average_entry_price` is a decimal; do not divide by `10000` again.
- [ ] Wallet balances are integer units; divide by `10000` for display.

### 7.5 Charts and activity

- [ ] Chart history `price` is already a decimal (`0..1`).
- [ ] Do not assume only the traded outcome gets a history point; every active
      outcome does.
- [ ] Handle a final `1.0` / `0.0` point after resolution.
- [ ] Activity `payload.prices` is keyed by string outcome ID.
- [ ] Use `payload.notional` for cash in trade events.

### 7.6 Market detail depth

- [ ] Label depth as "market maker liquidity" or "indicative depth", not
      "order book".
- [ ] Convert depth `price` ticks to decimals for display.
- [ ] Expect empty `bids`/`asks` for closed and resolved markets.
- [ ] Handle `404` for unknown outcomes.

### 7.7 Cache invalidation

After a successful order:

- `orders`
- `positions`
- `positions/summary`
- `wallet`
- `wallet/ledger`
- `market detail` (slug)
- `market stats` (slug)
- `order-book` (outcome ID)
- `trades` (outcome ID)
- `activity` (outcome ID)

After market close/resolve:

- `market detail` (slug)
- `market stats` (slug)
- `markets` list
- `positions`
- `wallet`
- `trades`
- `activity`
- all `order-book` queries for that market

---

## 8. Admin (`marketx-admin`) Update Checklist

### 8.1 Market-maker records appear in admin lists

The backend creates a house account on first trade:

- Email: `MARKETX_HOUSE_EMAIL` (environment-specific, e.g.
  `marketx@marketx.com`).
- Username: `MARKETX_HOUSE_USERNAME` (e.g. `marketx`).
- `is_active = false`, cannot log in.
- Its wallet `reserved_balance` is issuance collateral.

The house account now appears in:

- `GET /api/admin/users/`
- `GET /api/admin/wallets/`
- `GET /api/admin/orders/`
- `GET /api/admin/positions/`
- `GET /api/admin/trades/`

Required admin behavior:

- [ ] Label the house account as "Market Maker" / "Liquidity", not a customer.
- [ ] Exclude it from customer counts, leaderboards, and customer volume.
- [ ] Exclude or separate house positions; they are AMM inventory, not user
      exposure.
- [ ] Hide or separate orders whose `client_order_id` starts with `amm:`.
      These are synthetic AMM fills, not user orders.
- [ ] Do not offer a cancel action for `filled` orders or `amm:*` orders. The
      backend returns `200` unchanged, which is confusing in the UI.
- [ ] In trade tables, show the house counterparty as "Market Maker" rather
      than a user link.

If hardcoding the house email/username is undesirable, ask the backend to add
an `is_market_maker` boolean to admin user/order/trade/position payloads.

### 8.2 Admin order table

`GET /api/admin/orders/` fields are unchanged:

- `limit_price` is a slippage cap.
- `partially_filled` orders are terminal if `cancelled_at` is set.
- New orders are `filled` or `partially_filled`; `open` is legacy.
- `client_order_id` starting with `amm:` indicates an AMM synthetic order.

`POST /api/admin/orders/{id}/cancel/`:

- Legacy open order: cancels and releases reservations.
- Filled/AMM order: returns `200` unchanged.
- Unknown order: `404`.

### 8.3 Admin trade table

`GET /api/admin/trades/` currently returns `price`, `price_decimal`, and
`quantity`, but **does not return `notional`**. If the admin UI needs exact
cash, do not compute `price * quantity`; it can differ by rounding. Either:

- display only approximate cash with a note, or
- request the backend add `notional` / `notional_decimal` to
  `AdminTradeSerializer`.

Every new trade has the house as buyer or seller. Legacy user-to-user trades
may still exist in history.

### 8.4 Admin position table

`GET /api/admin/positions/` currently returns `quantity`, `cost_basis`,
`realized_pnl`, and `average_entry_price`, but not `current_price`,
`market_value`, or `unrealized_pnl`. If the admin UI needs live valuation,
request those fields from the backend rather than deriving them from stale
prices.

### 8.5 Admin overview metrics

`GET /api/admin/overview/`:

- `orders` and `trades` counts include AMM house activity.
- `open_orders` counts `open` + `partially_filled` **without checking
  `cancelled_at`**, so cancelled partial fills may be counted as open. Do not
  rely on this number until the backend fixes it.
- `open_positions` includes house inventory.

Recommended admin UI behavior:

- [ ] Label totals as "including market-maker activity".
- [ ] Compute customer-only metrics from filtered lists if needed.
- [ ] Do not show `open_orders` as a customer pending-orders count.

### 8.6 Admin market editing

- [ ] `liquidity_b` is writable by staff. Changing it immediately reprices all
      outcomes for the same net shares. Warn before saving or hide it from
      normal editors.
- [ ] `issuance_limit` and `issued_quantity` are **read-only** in the API.
      Do not present `issuance_limit` as an editable field; the backend ignores
      it.
- [ ] `MarketOutcome.net_shares` is read-only.
- [ ] `is_active` cannot change after issuance has started; handle the
      `non_field_errors` response.
- [ ] Outcomes can only be added while the market is `draft`.

### 8.7 Admin resolve flow

Payload is unchanged:

```json
{
  "winning_outcome": 12,
  "evidence_summary": "Official result published.",
  "evidence_url": "https://example.com/result"
}
```

For a void market:

```json
{
  "is_void": true,
  "evidence_summary": "Event cancelled."
}
```

After a successful resolve (`201`):

- Settlement is **synchronous**.
- Market status becomes `resolved` or `void`.
- Prices become `10000/0` (resolved) or frozen LMSR (void).
- Positions are settled and payouts credited.
  - Resolved: winner pays `quantity * 10000`, losers pay `0`.
  - Void: each position is refunded its `cost_basis` (not its frozen LMSR
    market value).
- Refetch market detail, stats, positions, wallet, trades, and activity.

---

## 9. Error Handling Matrix

| Situation | Status | Body shape |
|---|---|---|
| Field validation | `400` | `{"field": ["message"]}` |
| Service rejection | `400` | `{"detail": ["message"]}` |
| Non-field validation | `400` | `{"non_field_errors": ["message"]}` |
| Not authenticated | `401` | DRF auth error |
| Not staff/admin | `403` | DRF permission error |
| Unknown order on cancel | `404` | `{"detail": "Order not found."}` |
| Unknown outcome on depth | `404` | `{"detail": "Outcome not found."}` |
| Unknown market/outcome ID | `404` or `400` | DRF standard |
| Nothing fillable | `400` | `{"detail": ["No quantity can fill at this limit."]}` |
| Closed market order | `400` | `{"detail": ["Orders can only be placed on open markets."]}` |
| Closed market quote | `400` | `{"detail": ["Quotes are only available for open markets."]}` |
| Reused `client_order_id` | `400` | `{"detail": ["client_order_id was already used for a different order."]}` |
| Partial fill | `201` | Order with `status: "partially_filled"` and `cancelled_at` |
| Duplicate identical order | `201` | Original order |

**Important:** service errors use a **list** in `detail`. Write a normalizer:

```ts
export function errorMessage(body: unknown): string {
  if (!body || typeof body !== "object") return "Request failed."
  const record = body as Record<string, unknown>
  const detail = record.detail
  if (Array.isArray(detail)) return detail.join(" ")
  if (typeof detail === "string") return detail
  const firstField = Object.values(record)[0]
  if (Array.isArray(firstField)) return firstField.join(" ")
  if (typeof firstField === "string") return firstField
  return "Request failed."
}
```

---

## 10. Known Backend Gaps (Report to Backend Team)

These are not frontend bugs, but they affect what the clients can display:

1. `AdminTradeSerializer` does not expose `notional` / `notional_decimal`.
2. `AdminPositionSerializer` does not expose `current_price`,
   `market_value`, or `unrealized_pnl`.
3. `GET /api/admin/overview/` `open_orders` does not exclude
   `cancelled_at != null`, so cancelled partial fills may be counted as open.
4. Admin payloads do not expose an `is_market_maker` flag. Frontends must
   hardcode or configure the house email/username to filter AMM records.
5. `GET /api/markets/{slug}/stats/` `total_volume` is shares, not credits. If
   the admin dashboard needs credit volume, the backend should expose
   `total_notional`.
6. `liquidity_b` can be changed after issuance and immediately reprices the
   market. Consider freezing it after `issued_quantity > 0`.

---

## 11. Migration and Deployment Notes

Applied migrations:

- `apps/markets/migrations/0004_market_liquidity_b_marketoutcome_net_shares_and_more.py`
- `apps/trading/migrations/0002_trade_notional_trade_trade_nonnegative_notional.py`

Defaults for existing data:

- `Market.liquidity_b = 1000`
- `MarketOutcome.net_shares = 0`
- `Trade.notional = 0`

Implications:

- Existing markets start at uniform LMSR prices because `MarketOutcome.net_shares`
  defaults to `0`, even if they have legacy trades. Legacy positions are not
  reflected in the AMM's net-share counters.
- Existing legacy trades may have `notional = 0`; use
  `price * quantity` as a display fallback only.
- Existing legacy open orders may still appear; they can be cancelled.
- The house user/wallet is created lazily on the first trade. Before that,
  admin user/wallet lists will not include it.
- Existing positions are now valued by LMSR, not by old last-trade prices.

---

## 12. Smoke-Test Scenarios for Frontend Agents

Use these to verify the integration:

1. **Fresh binary market**
   - Market detail: `current_price` is `[5000, 5000]`.
   - `current_prices` sums to `10000`.
   - Stats `prices` sums to `1.0`.
2. **Market BUY without `limit_price`**
   - `POST /api/trading/orders/` with no `limit_price`.
   - Expect `201`, `status: "filled"`, `limit_price: 9999`.
   - Trade `notional` is exact; wallet drops by `notional`.
3. **Quote before submit**
   - Fresh market, BUY 10 YES.
   - Quote: `fillable_quantity: 10`, `notional: 50125`,
     `average_price: 5012`, `prices_after: {"12":5025,"13":4975}`.
   - Submit same order: filled quantity and notional match the quote.
4. **Limit below market**
   - BUY 1 at `limit_price: 4000` on a 50/50 market.
   - Quote: `200`, `fillable_quantity: 0`, `limited_by: "limit"`.
   - Order: `400`, `detail` is a list, no order created.
5. **Sell back to AMM**
   - After buying 10 YES, SELL 4 with no limit.
   - Expect `201`, `status: "filled"`.
   - Refund `notional: 20079`, `average_price: 5020`.
   - Prices move down to `[5015, 4985]`.
6. **Partial fill**
   - Use a wallet with enough credits for only part of the order, or an
     outcome whose AMM inventory/issuance is nearly exhausted.
   - Expect `201`, `status: "partially_filled"`, `cancelled_at` non-null,
     `remaining_quantity > 0`.
   - Do not show it as an active order.
7. **Resolved market**
   - Resolve with a winner.
   - Market detail prices: winner `10000` / `1.0`, losers `0` / `0.0`.
   - Positions settled, wallet credited, `realized_pnl` updated.
8. **Multi-outcome market**
   - Three or more outcomes.
   - All `current_price` values sum to `10000`.
   - All `prices` decimals sum to `1.0`.
   - Buying one outcome raises it and lowers the others.
9. **Admin filtering**
   - Place a trade.
   - Admin users/orders/positions/trades include the house account.
   - Confirm the UI labels or hides market-maker records.
10. **Legacy fallback**
    - If a trade has `notional === 0`, display `price * quantity` as an
      approximate fallback.

---

## 13. Do-Not-Do List

- Do not assume a seller must exist before a BUY.
- Do not assume orders rest or can be filled later.
- Do not poll an order expecting its status to change from `partially_filled`.
- Do not treat `limit_price` as the execution price.
- Do not compute cash as `limit_price * quantity`.
- Do not compute exact cash as `price * quantity` when `notional` is available.
- Do not sum per-outcome last trade prices.
- Do not use market-detail `latest_price` or position `last_price` as a trade
  tape value.
- Do not assume `current_price` is always `1..9999`; resolved values are
  `0` and `10000`, and a single active outcome is `10000`.
- Do not assume inactive outcomes have a price.
- Do not assume `stats.prices` is keyed by outcome ID; it is keyed by slug.
- Do not assume `quote.prices_after` is keyed by slug; it is keyed by string
  outcome ID.
- Do not show the house account or `amm:*` orders as customer activity.
- Do not derive live position value from `cost_basis` or `last_price`.
- Do not expose `liquidity_b` editing without a warning; it reprices the
  market.
- Do not expect `issuance_limit` changes through the API; it is read-only.

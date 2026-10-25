# MarketX Admin Dashboard API Guide

This document is the frontend implementation contract for the MarketX admin
dashboard. It explains the backend architecture, available endpoints, payloads,
expected frontend consumption patterns, and dashboard UX direction.

The dashboard stack is:

- Next.js, preferably App Router.
- TanStack Query for server state, caching, mutations, invalidation, and polling.
- React Context API only for global client state such as auth session context,
  dashboard shell state, theme preference, and ephemeral UI state.
- shadcn/ui for UI primitives and shadcn charts for charting.
- Framer Motion for small native-feeling transitions and micro interactions.

The dashboard should be mobile-first, excellent on desktop, minimalist, sleek,
and black-and-white by default. Think Apple-like operational software: precise
spacing, quiet surfaces, strong typography, soft motion, and very little visual
noise.

## Backend Architecture

MarketX is a Django REST Framework modular monolith.

Current backend modules:

- `apps.account`: custom user model, cookie JWT auth, Google auth, onboarding,
  user profile endpoints.
- `apps.wallet`: simulated virtual-credit wallet, immutable ledger, wallet
  balance APIs.
- `apps.markets`: market categories, markets, outcomes, resolutions, price
  history, market media upload, discovery/admin endpoints.
- `apps.trading`: orders, trades, positions, settlement, activity events,
  LMSR market-maker execution and quotes, order-book depth, portfolio
  endpoints, background tasks.
- `apps.notifications`: Firebase Cloud Messaging device-token registry.

Important domain rules:

- MarketX uses simulated virtual credits only.
- No real-money deposits, withdrawals, crypto, or gambling payments exist.
- All credit-like values are integer units.
- `CREDIT_SCALE = 10000`, meaning `10000` units equals one virtual payout
  credit.
- Prices are integer ticks from `1` to `9999`, and the active outcomes of a
  market always sum to `10000` (`100%`).
- A winning contract pays `10000` units.
- Quantities are whole contracts.
- Every market is priced by an LMSR market maker. The platform liquidity
  account is the sole counterparty; users trade against it, never with each
  other.
- A BUY mints complete sets when the market maker lacks inventory, sells the
  requested outcome, and keeps the complementary shares. `issuance_limit` caps
  minted sets per market.
- A SELL buys shares back from the user at the current LMSR refund. Naked short
  selling is not supported.
- `limit_price` is a slippage cap, not a resting price. Omit it for a market
  order: BUY defaults to `9999`, SELL to `1`. Unfilled remainders are cancelled
  immediately; orders never rest in an order book.
- PostgreSQL/Django ORM records are authoritative. UI caches are never
  authoritative.

## API Base And Auth

Assume the API base URL is provided through an environment variable:

```text
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

Most endpoints are under:

```text
{NEXT_PUBLIC_API_BASE_URL}/api/
```

Trading endpoints are under:

```text
{NEXT_PUBLIC_API_BASE_URL}/api/trading/
```

The backend uses cookie JWT authentication. The frontend API client must send
credentials:

```ts
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body)
  }

  return res.json()
}
```

For `FormData` uploads, do not set `Content-Type`; the browser must set the
multipart boundary:

```ts
await fetch(`${baseUrl}/api/markets/${slug}/upload_image/`, {
  method: "POST",
  credentials: "include",
  body: formData,
})
```

## Pagination

DRF list endpoints use page-number pagination by default:

```json
{
  "count": 42,
  "next": "http://127.0.0.1:8000/api/markets/?page=2",
  "previous": null,
  "results": []
}
```

Recommended frontend pattern:

- Use `useQuery` for ordinary paginated tables with `page` in the query key.
- Use `keepPreviousData` or placeholder data to prevent table flicker.
- Use `useInfiniteQuery` only for mobile activity feeds or logs where infinite
  scroll is natural.

Example query key:

```ts
["markets", { page, status, category, marketType, search, featured }]
```

## Data Types

Use these TypeScript types as the frontend baseline.

```ts
export type MarketStatus = "draft" | "open" | "closed" | "resolved" | "void"
export type MarketType = "binary" | "multi_outcome"
export type OrderSide = "buy" | "sell"
export type OrderStatus = "open" | "partially_filled" | "filled" | "cancelled"

export type UserProfile = {
  id: number
  email: string
  first_name: string
  last_name: string
  username: string
  avatar: string | null
  is_onboarded: boolean
  is_verified: boolean
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
  latest_price: number | null
  current_price: number | null
  current_price_decimal: number | null
}

export type Market = {
  id: number
  title: string
  slug: string
  market_type: MarketType
  question: string
  description: string
  category: MarketCategory
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
  created_at: string
  updated_at: string
}

export type MarketPrice = {
  outcome_id: number
  name: string
  slug: string
  price: number
  price_decimal: number
}

export type Order = {
  id: number
  outcome: number
  side: OrderSide
  limit_price: number
  original_quantity: number
  filled_quantity: number
  remaining_quantity: number
  status: OrderStatus
  client_order_id: string
  cancelled_at: string | null
  created_at: string
  updated_at: string
}

export type Trade = {
  id: number
  execution_id: string
  outcome: number
  price: number
  price_decimal: number
  quantity: number
  notional: number
  notional_decimal: number
  created_at: string
}

export type Position = {
  id: number
  outcome: number
  quantity: number
  reserved_quantity: number
  cost_basis: number
  realized_pnl: number
  average_entry_price: number | null
  last_price: number | null
  current_price: number | null
  current_price_decimal: number | null
  market_value: number | null
  unrealized_pnl: number | null
  settled_at: string | null
  updated_at: string
}

export type Wallet = {
  available_balance: number
  reserved_balance: number
  created_at: string
  updated_at: string
}
```

## Authentication Endpoints

### Login

```http
POST /api/auth/login/
```

Payload:

```json
{
  "email": "admin@example.com",
  "password": "password"
}
```

Success response:

```json
{
  "status": true,
  "message": "Token obtained successfully"
}
```

Cookies set by backend:

- `access_token`, HTTP-only.
- `refresh_token`, HTTP-only.

Frontend usage:

- Use `useMutation`.
- On success, invalidate `["me"]` and redirect to dashboard home.
- Keep auth state in a small `AuthContext` that reads `useMeQuery`.

### Google Login

```http
POST /api/auth/google/
```

Payload:

```json
{
  "token": "google-id-token-from-client"
}
```

Frontend usage:

- Use this only if the admin login UI supports Google OAuth.
- On success, same handling as standard login.

### Refresh Token

```http
POST /api/auth/refresh/
```

Payload: none. Backend reads `refresh_token` cookie.

Frontend usage:

- The API client may call this after a `401`, then retry the original request.
- Avoid infinite retry loops.

### Logout

```http
POST /api/auth/logout/
```

Payload: none.

Frontend usage:

- Use `useMutation`.
- On success, clear TanStack Query cache and route to `/login`.

### Current User

```http
GET /api/auth/me/
```

Response:

```json
{
  "id": 1,
  "email": "admin@example.com",
  "first_name": "Admin",
  "username": "admin",
  "last_name": "User",
  "avatar": null,
  "is_onboarded": true,
  "is_verified": true
}
```

TanStack Query:

```ts
useQuery({
  queryKey: ["me"],
  queryFn: () => apiFetch<UserProfile>("/api/auth/me/"),
  staleTime: 60_000,
})
```

Dashboard consumption:

- Gate admin routes on this query.
- The backend currently does not expose `is_staff` in this serializer. For true
  admin route guards, either add an admin profile endpoint or rely on backend
  `403` responses for staff-only actions.

### Update Profile

```http
PUT /api/auth/update/{id}/
PATCH /api/auth/update/{id}/
```

Payload:

```json
{
  "first_name": "Admin",
  "last_name": "User",
  "username": "admin",
  "avatar": "https://example.com/avatar.png"
}
```

Frontend usage:

- Usually not central to the admin dashboard.
- Use in account settings.

## Market Categories

### List Categories

```http
GET /api/categories/
```

Query params:

- `page`: number.

Frontend usage:

- Load for filter dropdowns and category management tables.
- Query key: `["market-categories", { page }]`.

### Create Category

```http
POST /api/categories/
```

Staff-only.

Payload:

```json
{
  "name": "Campus Sports",
  "slug": "campus-sports",
  "description": "Sports events around campus",
  "icon": "trophy",
  "is_active": true,
  "display_order": 10
}
```

Frontend usage:

- Use a shadcn `Dialog` or `Sheet` with `FieldGroup` and `Field`.
- On success, invalidate `["market-categories"]` and `["markets"]`.

### Update Category

```http
PATCH /api/categories/{slug}/
```

Payload may include any editable category field:

```json
{
  "name": "BUK Sports",
  "is_active": true,
  "display_order": 5
}
```

### Delete Category

```http
DELETE /api/categories/{slug}/
```

Use carefully. If markets reference the category, Django `PROTECT` may reject
the delete. Prefer setting `is_active: false` from the UI.

## Markets

### List Markets

```http
GET /api/markets/
```

Query params:

- `page`: number.
- `status`: `draft`, `open`, `closed`, `resolved`, `void`.
- `category`: category slug.
- `market_type`: `binary` or `multi_outcome`.
- `search`: text search over market question.
- `featured`: `true` or `1`.

Example:

```http
GET /api/markets/?status=open&category=campus-sports&page=1
```

Frontend usage:

- Main dashboard markets table.
- Use shadcn `Table` on desktop and compact cards/list rows on mobile.
- Use TanStack Query with filter state reflected in the URL search params.
- Suggested query key:

```ts
["markets", { page, status, category, marketType, search, featured }]
```

Recommended columns:

- Title/question.
- Status badge.
- Type.
- Category.
- Opens/closes.
- Featured.
- Outcome count.
- Quick actions: view, edit, publish, close, resolve, upload image.

### Retrieve Market

```http
GET /api/markets/{slug}/
```

Frontend usage:

- Market detail page.
- Use for admin review before publish/resolve.
- Query key: `["market", slug]`.

### Create Market

```http
POST /api/markets/
```

Staff-only.

Payload:

```json
{
  "title": "BUK Faculty Cup Winner",
  "market_type": "multi_outcome",
  "question": "Which faculty will win the BUK Faculty Cup?",
  "description": "Market resolves from the official sports directorate result.",
  "category_id": 1,
  "resolution_criteria": "The winning faculty announced by the BUK sports directorate is the winning outcome.",
  "resolution_source_description": "BUK Sports Directorate official announcement",
  "resolution_source_url": "https://example.edu/sports/results",
  "opens_at": "2026-10-20T09:00:00Z",
  "closes_at": "2026-10-25T18:00:00Z",
  "resolution_deadline": "2026-10-26T18:00:00Z",
  "is_featured": true
}
```

Notes:

- `status` is read-only through this serializer. New markets start as `draft`.
- `created_by` is inferred from the authenticated admin.
- `issuance_limit` and `issued_quantity` are read-only. The limit is the
  maximum number of complete sets the liquidity account may mint for this
  market, and `issued_quantity` is how many have been minted so far.
- Add outcomes separately before publishing.

Frontend usage:

- Multi-step create flow works best:
  1. Market basics.
  2. Resolution criteria.
  3. Dates.
  4. Outcomes.
  5. Review and publish.
- Use Framer Motion for step transitions.
- On success, route to `/admin/markets/{slug}`.

### Update Market

```http
PATCH /api/markets/{slug}/
```

Staff-only.

Payload example:

```json
{
  "title": "Updated title",
  "description": "Updated description",
  "is_featured": false,
  "resolution_source_url": "https://example.edu/updated-source"
}
```

Frontend usage:

- Use optimistic UI only for low-risk fields like `is_featured`.
- For lifecycle fields, wait for server success and invalidate market queries.

### Publish Market

```http
POST /api/markets/{slug}/publish/
```

Payload: none.

Rules:

- Market must be `draft`.
- Market must have at least two active outcomes.

Success response: full `Market`.

Frontend usage:

- Use `AlertDialog` for confirmation.
- On success, invalidate:
  - `["market", slug]`
  - `["markets"]`
  - dashboard stats queries.

### Close Market

```http
POST /api/markets/{slug}/close/
```

Payload: none.

Rules:

- Market must be `open`.
- Backend cancels remaining open orders and releases reservations.

Frontend usage:

- Use a destructive confirmation dialog.
- Explain that open orders will be cancelled.
- On success, invalidate market, order, activity, wallet, and positions queries.

### Resolve Or Void Market

```http
POST /api/markets/{slug}/resolve/
```

Resolve with winning outcome:

```json
{
  "winning_outcome": 12,
  "is_void": false,
  "evidence_summary": "Official result confirmed Faculty of Engineering as winner.",
  "evidence_url": "https://example.edu/results/faculty-cup"
}
```

Void market:

```json
{
  "is_void": true,
  "evidence_summary": "Event was cancelled and no objective result exists.",
  "evidence_url": "https://example.edu/event-cancelled"
}
```

Rules:

- Staff-only.
- `evidence_summary` is required.
- `winning_outcome` is required unless `is_void` is true.
- Settlement runs after resolution.

Frontend usage:

- Build as a high-friction flow:
  - show market details;
  - show outcomes;
  - require evidence summary;
  - require explicit confirmation.
- Use `AlertDialog`, `Select`, `Textarea`, and `Button`.
- On success, invalidate nearly all market financial state:
  - `["market", slug]`
  - `["markets"]`
  - `["positions"]`
  - `["trades"]`
  - `["wallet"]`
  - `["activity"]`

### Upload Market Image

```http
POST /api/markets/{slug}/upload_image/
Content-Type: multipart/form-data
```

Payload:

```text
image=<File>
```

Rules:

- Staff-only.
- File must be JPEG, PNG, or WEBP.
- Max size is 5MB.
- Cloudinary must be configured.

Frontend usage:

- Use a dropzone or file input in a `Dialog`/`Sheet`.
- Use `FormData`, not JSON.
- Show preview before upload.
- On success, invalidate `["market", slug]` and `["markets"]`.

## Outcomes

### List Outcomes

```http
GET /api/outcomes/
```

Query params:

- `page`: number.

Current backend returns active outcomes globally. For market-specific outcome
display, prefer `GET /api/markets/{slug}/`, because it embeds `outcomes`.

### Create Outcome

```http
POST /api/outcomes/
```

Staff-only.

Payload:

```json
{
  "market": 4,
  "name": "Faculty of Engineering",
  "slug": "engineering",
  "description": "Faculty of Engineering team",
  "image_url": "",
  "display_order": 1,
  "is_active": true
}
```

Rules:

- Outcomes can only be added while the market is `draft`.
- Outcome identity should not change after draft.

Frontend usage:

- Use in the market creation wizard.
- For binary markets, create two outcomes such as `Yes` and `No`.
- For multi-outcome markets, allow adding/reordering outcomes before publish.

### Update Outcome

```http
PATCH /api/outcomes/{id}/
```

Payload:

```json
{
  "name": "Engineering",
  "description": "Updated label",
  "display_order": 2,
  "is_active": true
}
```

Frontend usage:

- Only expose editing freely for draft markets.
- After publish, treat outcome identity as locked.

### Delete Outcome

```http
DELETE /api/outcomes/{id}/
```

Prefer `is_active: false` in the UI unless the outcome was created by mistake
before publication.

## Market Stats And Charts

### Market Stats

```http
GET /api/markets/{slug}/stats/
```

Response:

```json
{
  "latest_price": 0.42,
  "prices": { "yes": 0.42, "no": 0.58 },
  "liquidity_b": 1000,
  "total_volume": 120,
  "trade_count": 18,
  "first_trade_at": "2026-10-20T10:00:00Z",
  "last_trade_at": "2026-10-20T12:00:00Z",
  "depth": {
    "yes": {
      "current_price": 4200,
      "bids": [{ "price": 4100, "quantity": 5 }],
      "asks": [{ "price": 4300, "quantity": 3 }]
    }
  }
}
```

Frontend usage:

- Use `prices` for the unified price vector; it always sums to `1`.
- `latest_price` is a legacy field (last trade price) and may be `null` before
  the first trade. Prefer `prices` for display.
- Use for market detail header cards.
- Poll lightly for open markets, for example every `10_000` to `30_000` ms.
- Do not poll closed/resolved markets aggressively.

### Outcome Price History

```http
GET /api/outcomes/{id}/history/
```

Query params:

- `range`: `1H`, `6H`, `1D`, `1W`, `1M`, or `ALL`.
- `limit`: positive integer, max `1000`.
- `offset`: non-negative integer.

Response:

```json
{
  "data": [
    {
      "timestamp": 1783036800,
      "price": 0.42,
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

Frontend usage with shadcn charts:

- Use `ChartContainer`, `ChartTooltip`, and Recharts primitives through shadcn.
- Convert Unix timestamp to chart labels client-side.
- Use one line per selected outcome or one focused chart per outcome.
- Keep chart styling monochrome:
  - main line: foreground token;
  - grid/axis: muted token;
  - tooltip: popover/card token.
- Query key:

```ts
["outcome-history", outcomeId, { range, limit, offset }]
```

Recommended chart controls:

- Segmented `ToggleGroup`: `1H`, `6H`, `1D`, `1W`, `1M`, `ALL`.
- Skeleton while loading.
- Empty state when no trades exist.

## Trading Orders

### List Current Admin User Orders

```http
GET /api/trading/orders/
```

Query params:

- `page`: number.

Important:

- This endpoint returns only the authenticated user's orders.
- For a full admin-wide order-management table, the backend needs an additional
  staff endpoint. Until then, use trades/activity for global market oversight.

Frontend usage:

- Useful for admin testing and operational accounts.
- Query key: `["orders", { page }]`.

### Submit Order

```http
POST /api/trading/orders/
```

Payload:

```json
{
  "outcome": 12,
  "side": "buy",
  "limit_price": 4200,
  "quantity": 10,
  "client_order_id": "admin-ui-uuid-v4"
}
```

Rules:

- `side`: `buy` or `sell`.
- `limit_price`: optional integer `1..9999` slippage cap. Omit it for a market
  order (BUY defaults to `9999`, SELL to `1`).
- For a BUY, `limit_price` is the maximum average fill price. For a SELL, it is
  the minimum average fill price.
- `quantity`: positive integer.
- `client_order_id`: stable unique ID generated by frontend per logical submit.
- Reusing the same `client_order_id` with different payload is an error;
  repeating an identical submit returns the original order.
- The order executes immediately. If the cap, balance, position, issuance, or
  house liquidity allows only part of the quantity, the filled part settles and
  the remainder is cancelled (`status: "partially_filled"` with
  `cancelled_at` set).
- A `400` response means nothing was filled (for example the limit is below the
  market). Inspect `detail` for the reason.

Frontend usage:

- The dashboard may expose this only in a staff “simulate/test trading” panel,
  not as the core admin moderation workflow.
- Call the quote endpoint first to show the expected fill and average price.
- Generate `client_order_id` before mutation.
- Disable submit while pending.
- On success, invalidate:
  - `["orders"]`
  - `["order-book", outcomeId]`
  - `["market-stats", slug]`
  - `["wallet"]`
  - `["positions"]`

### Quote Order

```http
GET /api/trading/quote/?outcome={id}&side=buy&quantity=10
```

Query params:

- `outcome`: outcome ID.
- `side`: `buy` or `sell`.
- `quantity`: positive integer.
- `limit_price`: optional integer `1..9999`; defaults as above.

Response:

```json
{
  "outcome": 12,
  "outcome_name": "Yes",
  "market_id": 3,
  "market_slug": "will-it-rain",
  "side": "buy",
  "requested_quantity": 10,
  "fillable_quantity": 10,
  "notional": 50250,
  "notional_decimal": 5.025,
  "average_price": 5025,
  "average_price_decimal": 0.5025,
  "current_price": 5000,
  "current_price_decimal": 0.5,
  "prices_after": { "1": 5025, "2": 4975 },
  "limited_by": null
}
```

`limited_by` is `null`, `"limit"`, `"balance"`, `"position"`, or `"liquidity"`.
The quote is read-only and matches the eventual execution for the same state.

### Cancel Order

```http
DELETE /api/trading/orders/{id}/
```

Response: updated order.

Frontend usage:

- Use confirmation for non-test flows.
- On success, invalidate orders, wallet, positions, order book, and activity.

## Trades

### List Trades

```http
GET /api/trading/trades/
```

Query params:

- `page`: number.
- `outcome`: outcome ID.
- `mine`: `true` or `1` to show trades where current user is buyer or seller.

Response item:

```json
{
  "id": 1,
  "execution_id": "4dffbb62-d604-4d5d-83db-9898b5f40c2c",
  "outcome": 12,
  "price": 4200,
  "price_decimal": 0.42,
  "quantity": 10,
  "created_at": "2026-10-20T12:00:00Z"
}
```

Privacy note:

- Public trade serializer intentionally omits buyer and seller IDs.

Frontend usage:

- Use for global trade tape and market detail trade history.
- Poll open markets lightly.
- Query keys:
  - `["trades", { page, outcome }]`
  - `["my-trades", { page }]`

UI:

- Desktop: shadcn `Table`.
- Mobile: compact list with price, quantity, time.
- Use `Badge` for side only if side is added later; current public trade payload
  does not expose maker/taker side.

## Positions

### List Current User Positions

```http
GET /api/trading/positions/
```

Response: paginated `Position[]`.

Each position is valued at the current LMSR price for its outcome:
`current_price` is the integer tick, `market_value = quantity * current_price`,
and `unrealized_pnl = market_value - cost_basis`. `last_price` is a
backwards-compatible alias for `current_price`.

Important:

- This endpoint returns only authenticated user's positions.
- For full admin portfolio surveillance across all users, add a staff-only
  endpoint before building that table.

Frontend usage:

- Use for portfolio/admin account page.
- Query key: `["positions", { page }]`.

### Position Summary

```http
GET /api/trading/positions/summary/
```

Response:

```json
{
  "open_positions": 3,
  "total_cost_basis": 120000,
  "realized_pnl": 25000
}
```

Frontend usage:

- Summary cards.
- Convert integer units to display credits:

```ts
export function formatCredits(units: number) {
  return (units / 10000).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}
```

## Order Book

### Retrieve Depth

```http
GET /api/trading/order-books/{outcome_id}/
```

Query params:

- `limit`: positive integer, max `100`.

Response:

```json
{
  "current_price": 4200,
  "bids": [
    { "price": 4100, "quantity": 5 }
  ],
  "asks": [
    { "price": 4300, "quantity": 3 }
  ]
}
```

The depth is synthetic: it is derived from LMSR quotes against the market
maker, not from resting user orders. `current_price` is the unified LMSR tick
for the outcome. A closed or resolved market returns empty `bids` and `asks`.

Frontend usage:

- Use for market detail depth component.
- Poll open markets every `5_000` to `15_000` ms until WebSockets are added.
- Use mirrored bid/ask columns on desktop and stacked sections on mobile.
- Use monochrome bars with opacity/width differences rather than green/red.

Query key:

```ts
["order-book", outcomeId, { limit }]
```

## Activity Events

### List Activity

```http
GET /api/trading/activity/
```

Query params:

- `page`: number.
- `outcome`: outcome ID.

Response item:

```json
{
  "id": 1,
  "event_type": "trade_executed",
  "visibility": "public",
  "outcome": 12,
  "payload": {
    "trade_id": 1,
    "price": 4200,
    "quantity": 10
  },
  "created_at": "2026-10-20T12:00:00Z"
}
```

Event types currently written by backend:

- `order_placed`, private.
- `order_cancelled`, private.
- `order_filled`, private.
- `trade_executed`, public.
- `position_settled`, private.

Frontend usage:

- Activity feed on dashboard home.
- Market activity panel on market detail pages.
- Use `useInfiniteQuery` for a feed.
- Use `visibility` to choose subtle iconography; do not expose private events
  in public market views.

## Wallet

### Wallet Detail

```http
GET /api/wallet/
```

Response:

```json
{
  "available_balance": 100000,
  "reserved_balance": 20000,
  "created_at": "2026-10-20T12:00:00Z",
  "updated_at": "2026-10-20T12:05:00Z"
}
```

Frontend usage:

- Current admin/user wallet card.
- Display as simulated credits only.
- Never label balances as money.

### Wallet Ledger

```http
GET /api/wallet/ledger/
```

Response item:

```json
{
  "id": 1,
  "entry_type": "available_debit",
  "available_delta": -42000,
  "reserved_delta": 0,
  "reference_type": "trade",
  "reference_id": 87,
  "description": "Credits spent buying outcome shares",
  "created_at": "2026-10-20T12:00:00Z"
}
```

Buy orders debit the exact LMSR notional and sell orders credit the exact
refund, so wallet ledger entries now use `available_debit` and
`available_credit`. `order_reserve` entries only appear for legacy orders
created before the market maker migration.

Frontend usage:

- Ledger table in account or audit section.
- Use signed number formatting.
- Show entry type as `Badge`.

## Notifications

### List Devices

```http
GET /api/notifications/devices/
```

### Register Device

```http
POST /api/notifications/devices/
```

Payload:

```json
{
  "token": "fcm-device-token-from-frontend",
  "platform": "web"
}
```

Allowed platform values:

- `web`
- `android`
- `ios`

Frontend usage:

- Register after user grants notification permission.
- Invalidate `["notification-devices"]`.

### Deactivate Device

```http
DELETE /api/notifications/devices/{id}/
```

Backend soft-deactivates the device.

## Admin Dashboard Information Architecture

Recommended routes:

```text
/login
/admin
/admin/markets
/admin/markets/new
/admin/markets/[slug]
/admin/markets/[slug]/edit
/admin/trades
/admin/activity
/admin/categories
/admin/positions
/admin/wallet
/admin/notifications
/admin/settings
```

Recommended dashboard home:

- KPI strip:
  - open markets;
  - draft markets;
  - total trade count;
  - recent volume;
  - unresolved closed markets.
- Featured/open markets table.
- Recent trade tape.
- Recent activity feed.
- Quick actions:
  - create market;
  - create category;
  - reconcile note or link to operations docs.

Some KPIs are not yet exposed as single aggregate endpoints. The frontend can
derive small admin dashboard stats from paginated list metadata and market stats
initially, but a dedicated `/api/admin/stats/` endpoint would be better before
launch.

## TanStack Query Patterns

Recommended query keys:

```ts
export const queryKeys = {
  me: ["me"] as const,
  categories: (params: unknown) => ["market-categories", params] as const,
  markets: (params: unknown) => ["markets", params] as const,
  market: (slug: string) => ["market", slug] as const,
  marketStats: (slug: string) => ["market-stats", slug] as const,
  outcomeHistory: (outcomeId: number, params: unknown) =>
    ["outcome-history", outcomeId, params] as const,
  orderBook: (outcomeId: number, params: unknown) =>
    ["order-book", outcomeId, params] as const,
  trades: (params: unknown) => ["trades", params] as const,
  positions: (params: unknown) => ["positions", params] as const,
  positionSummary: ["positions", "summary"] as const,
  wallet: ["wallet"] as const,
  walletLedger: (params: unknown) => ["wallet-ledger", params] as const,
  activity: (params: unknown) => ["activity", params] as const,
}
```

Mutation invalidation examples:

- Create/update category:
  - invalidate `market-categories`;
  - invalidate `markets`.
- Create/update market:
  - invalidate `markets`;
  - invalidate `market(slug)` if known.
- Publish/close/resolve:
  - invalidate `markets`;
  - invalidate `market(slug)`;
  - invalidate `marketStats(slug)`;
  - invalidate `activity`;
  - invalidate `positions`;
  - invalidate `wallet`.
- Upload market image:
  - invalidate `markets`;
  - invalidate `market(slug)`.
- Submit/cancel order:
  - invalidate `orders`;
  - invalidate `orderBook(outcomeId)`;
  - invalidate `wallet`;
  - invalidate `positions`;
  - invalidate `activity`.

Use `sonner` toasts for mutation success/failure.

## UI And Design System Requirements

Follow all `SKILL.md` files added to the project. In particular, when using
shadcn/ui:

- Use existing shadcn components before custom markup.
- Use `Table`, `Card`, `Badge`, `Tabs`, `Sidebar`, `Dialog`, `Sheet`,
  `AlertDialog`, `Drawer`, `Command`, `Select`, `Input`, `Textarea`,
  `ToggleGroup`, `Skeleton`, `Empty`, `Tooltip`, and `sonner`.
- Use shadcn chart primitives for charts.
- Use `FieldGroup` and `Field` for forms.
- Use semantic tokens, not raw color classes.
- Use `gap-*`, not `space-x-*` or `space-y-*`.
- Use `Badge` for statuses.
- Use `Skeleton` for loading states.
- Use `Empty` for empty tables/feeds.
- Dialogs, Sheets, and Drawers must include accessible titles.

Visual direction:

- Monochrome black/white/gray palette.
- Avoid loud colored status systems. Prefer shape, text, opacity, and subtle
  badges.
- Use restrained borders and background contrast.
- Use dense but readable admin layouts.
- Mobile-first:
  - bottom or collapsible navigation;
  - stacked cards;
  - compact filters in drawers;
  - tables transform into list rows.
- Desktop:
  - persistent sidebar;
  - data tables;
  - split panes for market detail + activity/depth;
  - keyboard-friendly command palette.

Framer Motion:

- Use small transitions, 120ms to 220ms.
- Animate route section entry, dialog content, row insertion, filter drawers,
  and chart range changes.
- Avoid large decorative animations.
- Respect reduced-motion preferences.

## Dashboard Component Suggestions

Use these components as implementation targets:

- `DashboardShell`
- `DashboardSidebar`
- `TopCommandBar`
- `MetricCard`
- `MarketsTable`
- `MarketStatusBadge`
- `MarketForm`
- `OutcomeEditor`
- `ResolutionDialog`
- `MarketImageUploadDialog`
- `TradeTape`
- `OrderBookDepth`
- `OutcomePriceChart`
- `ActivityFeed`
- `WalletSummaryCard`
- `LedgerTable`
- `CategoryManager`
- `DeviceManager`

## Known Backend Gaps For Frontend Planning

The current backend is enough for a strong admin prototype, but a complete
control room would benefit from these additional endpoints:

- Staff-only list users endpoint with search/filter and staff-safe fields.
- Staff-only retrieve user endpoint.
- Staff-only all orders endpoint.
- Staff-only all positions endpoint.
- Staff-only wallet lookup by user.
- Admin analytics aggregate endpoint, for dashboard KPIs.
- WebSocket/Channels endpoints for real-time updates.
- Dedicated audit-log endpoint for admin lifecycle actions.

Until those exist:

- Use existing market/trade/activity endpoints for global oversight.
- Treat `orders`, `positions`, and `wallet` endpoints as current-user scoped.
- Keep admin-only UI actions behind backend staff-only endpoints and handle
  `403` cleanly.

## Error Handling

DRF validation errors usually look like:

```json
{
  "field_name": ["Error message."]
}
```

Generic errors may look like:

```json
{
  "detail": "Error message."
}
```

Frontend guidance:

- Map field errors into form fields.
- Show generic errors through `sonner`.
- For staff-only actions, handle `403` with a clear “You do not have permission”
  state.
- For `401`, try refresh once, then route to login.

## Formatting Helpers

```ts
export const CREDIT_SCALE = 10000

export function formatPriceTicks(price: number) {
  return `${((price / CREDIT_SCALE) * 100).toFixed(1)}%`
}

export function formatCredits(units: number) {
  return (units / CREDIT_SCALE).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
```

## Frontend Build Priority

1. Auth shell and protected admin layout.
2. Market list/detail/create/edit flows.
3. Outcome editor and publish flow.
4. Market stats, shadcn charts, order-book depth, trade tape.
5. Resolve/void flow with evidence capture.
6. Category manager.
7. Activity feed.
8. Wallet/ledger and current-user positions.
9. Notification device registration.
10. Add staff-wide users/orders/positions screens after backend endpoints exist.

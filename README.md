# GlowBug

A personal finance manager focused on multi-currency accounts, dual-currency
visualisations, and a dashboard you can actually edit.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- CPython 3.14 free-threaded (`3.14t`)
- Docker + Docker Compose (for PostgreSQL and Redis)

## Local setup

```bash
uv python install 3.14t
uv python pin 3.14t
uv lock
uv sync --frozen

cd frontend
npm ci
npm run build
cd ..

docker compose up -d db
uv run alembic upgrade head
uv run glowbug currency seed

uv run pytest
```

Start the development server:

```bash
uv run uvicorn glowbug.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) for the first-run setup:

1. Create your local account and pick a primary currency (and an optional secondary).
2. Add your first bank, cash, or savings accounts with opening balances.
3. The dashboard shows native balances and converts them to your primary and secondary currencies.

- `GET /healthz` — health plus whether the GIL is disabled
- `GET /api/currencies?q=pkr` — search the ISO currency catalog
- `GET /api/fx/rate?base=EUR&quote=USD&as_of=2026-11-26` — historical rate
- `GET /api/fx/convert?amount=100&base=EUR&quote=USD` — decimal conversion

Missing exchange rates return `404` or an explicit “Incomplete — missing rate”
label. They are never silently treated as `1.0`.

## Dashboard

The default dashboard is editable:

- **Edit dashboard** enables drag, resize, add, remove, and per-widget settings.
- Widgets: net worth, account balances, income vs expenses, cash flow, recent
  transactions, and exchange rate.
- Global range presets: 1M, 3M, 6M, YTD, 1Y, All.
- Layouts are saved per dashboard; drag/resize changes persist automatically.

The dashboard is a small Vite island in `frontend/` using Gridstack and Chart.js.
Rebuild it after frontend changes:

```bash
cd frontend
npm run build
```

## Transactions and categories

- `/transactions`: filter by range/category/uncategorized, create withdrawals,
  deposits, and transfers, and assign categories inline.
- Default categories are seeded per user: Bills, Food & dining, Groceries,
  Leisure, Travel, Transport, Health, Shopping, Housing, Utilities,
  Subscriptions, and more.
- Create custom categories at `/categories`.
- The dashboard includes a **Spending by category** widget, and the recent
  transactions widget shows each transaction's category.

## CSV import

- `/transactions/import`: upload a statement, map its columns, preview parsed
  rows, then import.
- Imports are **idempotent**: each row gets a SHA-256 fingerprint and re-importing
  the same statement skips existing rows.
- After import, recurring payment patterns are detected and shown in a review
  page where you can register them as subscriptions.
- Uploaded files are temporary and stored under `storage/imports/`.

## Subscriptions

- `/subscriptions`: recurring payments with amount and currency, frequency
  (weekly, monthly, quarterly, yearly), next due date, category, and account.
- Detected subscriptions are never created automatically; you confirm them.
- Duplicate registrations are prevented by a merchant + amount + currency +
  frequency signature.

## Category inference

Transaction categories are inferred without regex rules and without an LLM:

1. **Merchant memory** — exact normalized descriptions you have confirmed.
2. **Fuzzy merchant matching** — catches small spelling and suffix variations.
3. **Character n-gram Naive Bayes** — a small local model trained from your
   confirmed merchants, including enough context to learn patterns such as
   “subscription”, “fuel”, or “market”.

When confidence is low, the transaction stays uncategorized and the transactions
page asks you to choose. Selecting a category with **Remember** stores the
merchant attribution, applies it to matching uncategorized transactions, and
feeds the next model update. Local shops are learned from these confirmations;
the system improves as you use it. The inference layer is intentionally
replaceable, so a small embedding model can be added later without changing the
UI or merchant-memory data.

## Exchange rates

Rates come from **Frankfurter v2** only. There is no fallback provider.

- Weekly sync: Monday 06:00 UTC via the Taskiq scheduler.
- Manual runs:

```bash
uv run glowbug fx sync
uv run glowbug fx status
```

- The last successful sync is shown as a small footer note on the dashboard.
- If a sync fails, existing rates remain in place and the dashboard keeps showing
  “Incomplete — missing rate” until the next successful sync.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

## Dependency policy

Versions are pinned exactly in `pyproject.toml` and locked transitively in
`uv.lock`. Always use `uv`:

```bash
uv add --bounds exact <package>
uv lock --upgrade-package <package>
uv sync --frozen
```

Do not use `pip install`.

## Repository layout

```text
src/glowbug/     application code
tests/           test suite
migrations/      Alembic migrations
frontend/        React dashboard island (added in Phase 2)
```

The dashboard builder is a small Vite bundle in `frontend/` (Gridstack +
Chart.js). `npm run build` writes to `src/glowbug/web/static/build/`; Docker
builds it automatically.

The `firefly-iii/` directory is a local reference and is intentionally ignored
by git. It is not part of this repository or its release artifacts.

## License

See [LICENSE](LICENSE).

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

docker compose up -d db
uv run alembic upgrade head
uv run glowbug currency seed

uv run pytest
```

Start the development server:

```bash
uv run uvicorn glowbug.main:app --reload
```

- `GET /` — service identity
- `GET /healthz` — health plus whether the GIL is disabled
- `GET /api/currencies?q=pkr` — search the ISO currency catalog
- `GET /api/fx/rate?base=EUR&quote=USD&as_of=2026-11-26` — historical rate
- `GET /api/fx/convert?amount=100&base=EUR&quote=USD` — decimal conversion

Missing exchange rates return `404`; they are never silently treated as `1.0`.

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

The `firefly-iii/` directory is a local reference and is intentionally ignored
by git. It is not part of this repository or its release artifacts.

## License

See [LICENSE](LICENSE).

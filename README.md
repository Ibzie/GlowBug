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
uv run pytest
```

Start the development server:

```bash
uv run uvicorn glowbug.main:app --reload
```

- `GET /` — service identity
- `GET /healthz` — health plus whether the GIL is disabled

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

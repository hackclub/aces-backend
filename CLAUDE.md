# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Running the Application

```bash
# Docker (recommended for development)
docker-compose up

# Local (without Docker)
venv/bin/python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Dependencies

```bash
# Install/sync dependencies (uses uv)
uv sync
```

### Database Migrations

```bash
# Apply migrations
venv/bin/python -m alembic upgrade head

# Create a new migration
venv/bin/python -m alembic revision --autogenerate -m "description"
```

### Linting and Formatting

```bash
ruff check .          # lint
ruff format .         # format
ruff check --fix .    # auto-fix lint issues
```

### Tests

```bash
docker compose run --rm test           # run tests in Docker (recommended)
venv/bin/python -m pytest tests/       # run tests locally
./scripts/setup-test-db.sh             # create test DB if needed
```

Tests use `aces_test` database. External services (Hackatime, Airtable) are mocked. Models use PostgreSQL-specific types (ARRAY, JSONB) so SQLite cannot be used.

## Architecture

**FastAPI + SQLAlchemy async + PostgreSQL + Airtable + Redis**

### Request Flow

```
HTTP Request
  → CORS / Cloudflare IP / Rate Limiting / Logging middleware
  → Route handler (api/v1/*)
  → SQLAlchemy async session (PostgreSQL)
  → External services (Airtable, Hackatime) as needed
```

### Directory Layout

- `main.py` — App setup: middleware stack, lifespan (migrations + background jobs), route registration
- `api/v1/` — Route handlers grouped by domain: `auth/`, `users/`, `projects/`, `devlogs/`, `admin/`
- `models/main.py` — SQLAlchemy ORM models: `User`, `UserProject`, `Devlog`
- `db/main.py` — Async engine, session factory, migration runner
- `lib/` — Shared utilities: Hackatime API client, rate limiting, response models
- `jobs/` — Background tasks run on a loop via lifespan:
  - `devlogreview.py` — Syncs devlog review decisions from Airtable (every 10 min)
  - `pyramidsync.py` — Syncs user data to Airtable Pyramid table (every 10 min)
  - `usercleanup.py` — Purges users marked for deletion (every 24h)
- `migrations/` — Alembic migration versions

### Authentication

JWT-based auth via Hack Club Community Auth (OAuth2). Tokens have 7-day expiry. OTP-based signup/login goes through Airtable. The `get_current_user` dependency (in `api/v1/auth/`) is used across all protected routes. Permissions are stored as an array of SmallIntegers on the User model; `ADMIN` is the only permission currently defined.

### Card Awarding

Cards are awarded in `jobs/devlogreview.py` when a devlog is approved. Formula: `cards_awarded = hours_delta * cards_per_hour`. Default `CARDS_PER_HOUR = 8`. The multiplier can be adjusted per-devlog via Airtable.

### Known Issues

- Devlog `state` column has `default=0` (integer) — should be a string like `"Pending"`
- `update_project` handler has a bug: partial PATCH clears unset `Optional` fields instead of preserving existing values
- `venv/` is used (not `.venv`) due to permission issues with `.venv`

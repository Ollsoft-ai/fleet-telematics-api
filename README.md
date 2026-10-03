# Fleet Telematics API

> Demo application for the Compliance Copilot product video.

Backend for a connected construction-equipment fleet. Excavators, wheel loaders and rollers push telemetry (position, engine hours, fuel level, hydraulic pressure, J1939 fault codes); fleet managers and rental desks query it through the portal API.

## Run locally

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and FLEET_JWT_SECRET
docker compose up -d --build
```

The API listens on http://localhost:8000. Interactive docs are at `/docs` in every environment except `production`.

Without Docker (needs a running PostgreSQL):

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Configuration

All settings are environment variables with the `FLEET_` prefix. They can also be set in `.env`.

| Variable | Default | Notes |
|---|---|---|
| `FLEET_DATABASE_URL` | required | `postgresql+asyncpg://user:pass@host:5432/db` |
| `FLEET_JWT_SECRET` | required | At least 32 characters |
| `FLEET_ENVIRONMENT` | `production` | `development`, `staging` or `production` |
| `FLEET_LOG_LEVEL` | `INFO` | |
| `FLEET_ACCESS_TOKEN_TTL_MINUTES` | `30` | Portal token lifetime |
| `FLEET_LOGIN_MAX_FAILED_ATTEMPTS` | `5` | |
| `FLEET_LOGIN_LOCKOUT_MINUTES` | `15` | |
| `FLEET_TELEMETRY_RETENTION_DAYS` | `180` | |
| `FLEET_CORS_ALLOW_ORIGINS` | `[]` | JSON list of portal origins |

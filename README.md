# Fleet Telematics API

> Demo application for the Compliance Copilot product video.

Backend for a connected construction-equipment fleet. Excavators, wheel loaders and rollers push telemetry (position, engine hours, fuel level, hydraulic pressure, J1939 fault codes); fleet managers and rental desks query it through the portal API.

## What it does

- **Telemetry ingest**: machines post batches with a per-machine API key. Resent samples are de-duplicated.
- **Fault codes**: J1939 SPN/FMI tracking, with severity taken from the lamp status. The active set follows DM1.
- **Operators**: badge + PIN sign-in on the machine terminal, with lockout after repeated wrong PINs.
- **Geofences**: job-site and restricted-area polygons with enter/exit events.
- **Service intervals**: 250/500/1000/2000 h and critical-fault service events, delivered to the dealer system over a signed HTTPS webhook.
- **Reports**: utilization and active-fault summaries as JSON, plus a CSV export.
- **Retention**: telemetry, geofence events and operator sessions are deleted after `FLEET_TELEMETRY_RETENTION_DAYS`.

## Run locally

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and FLEET_JWT_SECRET
docker compose up -d --build
docker compose exec api python -m app.cli init-db
docker compose exec api python -m app.cli create-admin \
    --email you@example.com --name "Your Name" --organization "Your Company"
```

The API listens on http://localhost:8000. Interactive docs are at `/docs` in every environment except `production`.

Without Docker (needs a running PostgreSQL):

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
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
| `FLEET_LOGIN_MAX_FAILED_ATTEMPTS` | `5` | Applies to portal passwords and terminal PINs |
| `FLEET_LOGIN_LOCKOUT_MINUTES` | `15` | |
| `FLEET_TELEMETRY_RETENTION_DAYS` | `180` | Used by `purge-telemetry` |
| `FLEET_CORS_ALLOW_ORIGINS` | `[]` | JSON list of portal origins |
| `FLEET_DEALER_WEBHOOK_URL` | unset | HTTPS only. Dealer sync is off when unset |
| `FLEET_DEALER_WEBHOOK_SECRET` | unset | HMAC key, required when the URL is set |
| `FLEET_DEALER_TIMEOUT_SECONDS` | `10` | |
| `FLEET_DEALER_MAX_RETRIES` | `3` | Per delivery attempt |

## Scheduled jobs

- `python -m app.cli purge-telemetry`: nightly.
- `python -m app.cli sync-dealer`: every 5 minutes. Retries service events the dealer did not accept.

## API

| Area | Endpoints | Auth |
|---|---|---|
| Auth | `POST /auth/token`, `GET /auth/me` | none / bearer |
| Machines | `GET/POST /machines`, `GET/PATCH /machines/{id}`, `POST /machines/{id}/api-key` | bearer |
| Location shares | `POST /machines/{id}/shares` (manager/admin), `GET /shares` (24-hour browser link), `GET /shares/location` | bearer / none / share token header |
| Telemetry | `POST /telemetry` | machine key |
| | `GET /machines/{id}/telemetry`, `GET /machines/{id}/faults` | bearer |
| Operators | `GET/POST /operators`, `PATCH /operators/{id}` | bearer |
| | `POST /operator-sessions`, `POST /operator-sessions/{id}/end` | machine key |
| Geofences | `GET/POST /geofences`, `GET/PUT/DELETE /geofences/{id}`, `GET /geofences/{id}/events` | bearer |
| Reports | `GET /reports/utilization`, `GET /reports/utilization.csv`, `GET /reports/faults` | bearer |

Machines authenticate with the `X-Machine-Key` header. Portal users send `Authorization: Bearer <token>`.
The share creation response contains a URL and expiry time. Customers can open the URL to see the machine's latest coordinates and last-seen timestamp, refreshed every 15 seconds. API clients can pass the URL fragment's `token` as `X-Share-Token` to `GET /shares/location`. Treat the link as a secret.

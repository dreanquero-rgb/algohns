# Deploying Algohns

The platform is a Python app (scipy / numpy / pandas / Streamlit). The compute —
the bond engine, the Nelson-Siegel curve fit, the Monte Carlo — **must** run on
a Python server; it cannot run in a Cloudflare Worker (those execute JS/WASM
only). So "which host" is really "which Python host".

## TL;DR — what to use

| Goal | Use | Cold start | Cost |
|------|-----|-----------|------|
| Demo on your own screen | **Docker on your PC** | none (warm) | free |
| Always-on public URL | **Docker image on Render / Railway / Fly / a VPS** | none on a paid plan | €0–7/mo |
| Zero-effort crash fix only | **Pin Python 3.11 on Streamlit Cloud** | still sleeps | free |

The one thing to know: **Streamlit *Cloud* is the slow/broken part, not the
app.** Its free tier sleeps (≈20 s cold start) and runs bleeding-edge Python
3.14, whose Arrow path crashed the order journal. This repo's `Dockerfile` pins
Python 3.11 and any container host keeps it warm — which removes both problems
without changing a line of the app.

## Option A — Docker on your PC (best for the presentation)

```bash
cp .env.example .env          # put your Alpaca paper keys in .env
docker compose up -d --build  # dashboard + redis + worker + beat
```
Open http://localhost:8501 . Instant, warm, Python 3.11, and the auto-trading
worker runs too. Stop with `docker compose down`.

## Option B — always-on public URL (Render, one click)

1. Push this repo to GitHub (already done).
2. On https://render.com : **New + → Blueprint**, select the repo. Render reads
   `render.yaml`, builds the Dockerfile, and injects `$PORT`.
3. In the service's **Environment**, set `ALPACA_API_KEY` and
   `ALPACA_SECRET_KEY` (marked `sync: false`, so they live only in Render).
4. Free plan sleeps after ~15 min idle; switch the plan to **Starter** for
   always-on.

Railway and Fly.io work the same way from the same Dockerfile:
- **Railway**: New Project → Deploy from repo → it detects the Dockerfile.
- **Fly.io**: `fly launch` (accepts the Dockerfile), `fly secrets set ALPACA_API_KEY=... ALPACA_SECRET_KEY=...`, `fly deploy`.
- **VPS** (Hetzner ~€4/mo, etc.): install Docker, `git clone`, fill `.env`,
  `docker compose up -d`. This is the cheapest genuinely always-on option and
  runs the trading worker as well.

## Option C — stay on Streamlit Cloud but stop the crash

In the app's settings on share.streamlit.io, set the **Python version to 3.11**
(or 3.12). That alone fixes the Python-3.14 Arrow crash. It does **not** fix the
cold start — the free tier still sleeps — so warm it a few minutes before you
present.

## Note on the trading worker

The background worker that rebalances on a schedule needs a host that stays up
(Docker on your PC, a VPS, or a Render/Railway *worker* service). Streamlit
Cloud cannot run it — it has no persistent background process. See the
`worker`/`beat` services in `docker-compose.yml`.

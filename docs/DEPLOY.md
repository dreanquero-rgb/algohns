# Deploying Algohns V12

The V12 platform is a **Python / Streamlit** application. It cannot run inside a
Cloudflare Worker (Workers execute JS/WASM in a V8 isolate and cannot host a
long-lived Python server with native deps like NumPy/SciPy/QuantLib). The
recommended setup is:

1. **Host the dashboard** on Streamlit Community Cloud (free) — or any Python host.
2. **Keep the `algohns.dreanquero.workers.dev` domain** by turning the Cloudflare Worker
   into a redirect that forwards to the live app.

---

## Part 1 — Host the dashboard on Streamlit Community Cloud

1. Push this repo to GitHub (done — branch `main`).
2. Go to <https://share.streamlit.io> and sign in with GitHub.
3. **New app → From existing repo**:
   - Repository: `dreanquero-rgb/algohns`
   - Branch: `main`
   - Main file path: `app.py`
4. **Advanced settings → Secrets** — paste your configuration (TOML):
   ```toml
   ALPACA_API_KEY = "…"
   ALPACA_SECRET_KEY = "…"
   ALPACA_PAPER = "true"
   SEC_USER_AGENT = "Your Name your.email@example.com"
   DEFAULT_TAX_RESIDENCE = "IT"
   ```
   Streamlit exposes these as environment variables, which `algohns/config/settings.py` reads.
5. **Deploy.** You get a URL like `https://algohns.streamlit.app`.

### Requirements: lean vs full
- **`requirements.txt` is the LEAN, Cloud-safe set** — it installs cleanly on the
  ~1 GB Community Cloud builder (verified: no compilation, all wheels). This is
  what Streamlit Cloud installs automatically. The platform boots fully on it;
  every heavy feature degrades gracefully and each page shows what to add.
- **`requirements-full.txt` enables every feature** (local / Docker): it adds the
  build-fragile or heavy extras deliberately kept off Cloud:
  - `QuantLib` — bond cross-check (pure-python engine works without it).
  - `PyPortfolioOpt` + `cvxpy` — convex optimizers (a NumPy optimizer fallback is
    built in, so Max-Sharpe/Min-Var/Risk-Parity still work on Cloud).
  - `spaCy` — Module 4 NER (RegEx extraction works without it).
  - `celery[redis]`, `redis`, `APScheduler` — background workers (not runnable on
    Streamlit Cloud anyway; use Docker — see Part 3).
  - `ffn` — extended performance stats cross-check.
- `borsa-italiana-scraping` is GPL-3.0 and **not on PyPI**; Module 1 uses a
  built-in requests+BeautifulSoup scraper instead, so it is not required.

---

## Part 2 — Point the Cloudflare Worker at the app (redirect)

The Worker (`_worker.js`) now redirects every request to the URL in `APP_URL`.

```bash
npm install                       # installs wrangler
wrangler login                    # your Cloudflare account

# Set the live app URL (either as a var in wrangler.toml or as a secret):
wrangler secret put APP_URL       # paste https://algohns.streamlit.app
#   …or edit [vars] APP_URL in wrangler.toml

npm run deploy                    # wrangler deploy --name algohns
```

Verify:
```bash
curl -I https://algohns.dreanquero.workers.dev/           # -> 302 Location: https://algohns.streamlit.app/
curl  https://algohns.dreanquero.workers.dev/__redirect_health   # -> {"ok":true,"target":"…"}
```

Until `APP_URL` is set, the Worker serves a branded "coming online" landing page.
The legacy V11 application is preserved at `legacy/_worker_v11.js`.

---

## Part 3 — Alternative: full stack via Docker (worker + beat included)

For continuous background auto-trading (Celery + Redis), host the container
stack instead of Streamlit Cloud:

```bash
cp .env.example .env      # fill in Alpaca paper keys etc.
docker compose up --build # dashboard :8501 + redis + celery worker + beat
```

Deploy the same `Dockerfile` to Render, Railway, Fly.io or any container host.
Point the Cloudflare `APP_URL` at that host's URL exactly as in Part 2.

# Deploying Algohns V12

The V12 platform is a **Python / Streamlit** application. It cannot run inside a
Cloudflare Worker (Workers execute JS/WASM in a V8 isolate and cannot host a
long-lived Python server with native deps like NumPy/SciPy/QuantLib). The
recommended setup is:

1. **Host the dashboard** on Streamlit Community Cloud (free) — or any Python host.
2. **Keep the `algohns.workers.dev` domain** by turning the Cloudflare Worker
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

### Notes on resources
- Streamlit Community Cloud has ~1 GB RAM. Every heavy dependency in Algohns is
  imported *lazily*, so the app boots fine; if the build hits a resource/time
  limit, trim optional extras from `requirements.txt` — the platform degrades
  gracefully and each page shows what to reinstall:
  - `celery[redis]`, `redis` — background workers don't run on Streamlit Cloud
    anyway (use APScheduler locally, or a Docker host — see Part 3).
  - `QuantLib` — only the bond cross-check; the pure-python engine still works.
  - `spacy` — Module 4 falls back to RegEx-only extraction.
- For spaCy NER on Cloud, uncomment the `en_core_web_sm` line in `requirements.txt`.

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
curl -I https://algohns.workers.dev/           # -> 302 Location: https://algohns.streamlit.app/
curl  https://algohns.workers.dev/__redirect_health   # -> {"ok":true,"target":"…"}
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

# Deploy Algohns on Hugging Face Spaces (free, 16 GB RAM)

Hugging Face Spaces runs the Streamlit app for free with **16 GB of RAM** — 16×
what Streamlit Community Cloud gives — which is why the heavy pages
(FinanceDatabase universe, backtests, graphs) no longer run out of memory.

The repo is wired so that **every push to `main` auto-deploys** to the Space,
via `.github/workflows/huggingface-sync.yml`. You do the one-time setup below
once, then forget about it.

---

## One-time setup (≈5 minutes)

### 1. Create the Space
1. Go to <https://huggingface.co/new-space> (sign in / sign up — free).
2. **Owner**: your account (e.g. `dreanquero-rgb`).
3. **Space name**: `algohns`.
4. **SDK**: choose **Streamlit**.
5. **Hardware**: **CPU basic — free** (2 vCPU, 16 GB RAM).
6. Visibility: **Public** (private also works on free).
7. Create. (You'll get an empty Space — the GitHub Action fills it.)

> If you name it anything other than `dreanquero-rgb/algohns`, also do step 4
> below so the Action knows where to push.

### 2. Create a Hugging Face write token
1. <https://huggingface.co/settings/tokens> → **New token**.
2. Type: **Write**. Name it e.g. `github-deploy`. Copy the value (`hf_…`).

### 3. Add the token to GitHub
1. In this repo: **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `HF_TOKEN`  ·  Value: the `hf_…` token. Save.

### 4. (Only if your Space name differs) set the target
Same page → **Variables** tab → **New repository variable**:
- `HF_USERNAME` = your HF owner (e.g. `dreanquero-rgb`)
- `HF_SPACE` = your Space name (e.g. `algohns`)

### 5. Add your Alpaca keys to the Space (not to git)
On the Space: **Settings → Variables and secrets → New secret**. Add what you
use — they arrive as environment variables, which `app.py` reads automatically:

| Secret | Value |
|---|---|
| `ALPACA_API_KEY` | your paper key |
| `ALPACA_SECRET_KEY` | your paper secret |
| `ALPACA_PAPER` | `true` |

(Everything else is optional — see `.env.example`.)

---

## Deploy

Just push to `main` (or run the workflow manually):

- **Automatic**: any push to `main` triggers *Deploy to Hugging Face Spaces*.
- **Manual**: GitHub → **Actions → Deploy to Hugging Face Spaces → Run workflow**.

Watch it build on the Space page (**Logs** tab). First build takes a few
minutes while it installs `requirements.txt` on Python 3.11. Your app is then
live at:

```
https://huggingface.co/spaces/<owner>/<space>
# direct app URL:
https://<owner>-<space>.hf.space
```

---

## Why this fixes what Streamlit Cloud couldn't

- **RAM**: 16 GB vs 1 GB — the heavy modules stop OOM-ing.
- **Python pinned to 3.11** (via the `python_version` field in `README.md`
  front-matter) — avoids the pyarrow / Arrow type-inference break on
  bleeding-edge Python that the `Dockerfile` already warned about.
- **No `[server]` overrides** in `.streamlit/config.toml` — those broke the
  WebSocket handshake behind Streamlit Cloud's proxy and hung the app on the
  loading spinner forever.

## Point the old domain here (optional)
`wrangler.toml` still redirects `algohns.dreanquero.workers.dev` to `APP_URL`.
Set `APP_URL` to your Space's direct URL (`https://<owner>-<space>.hf.space`)
and `wrangler deploy` to keep the same entry point.

## Alternative: Docker Space
If you ever need system build-deps (QuantLib, cvxpy, spaCy from
`requirements-full.txt`), switch the Space to the **Docker** SDK — it reuses the
repo's `Dockerfile` as-is. Set `app_port: 8501` in the `README.md` front-matter
and change `sdk: streamlit` to `sdk: docker`.

# Algohns V12 — container image for the Streamlit dashboard.
#
# Pinned to Python 3.11 on purpose: Streamlit Cloud runs bleeding-edge Python
# (3.14 at time of writing), whose pyarrow/Arrow type-inference path breaks on
# object columns. This image sidesteps that entirely — and any container host
# (Render, Railway, Fly.io, a VPS) uses THIS file, unlike Streamlit Cloud which
# ignores it. Those hosts stay warm, so there is no cold start either.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# System deps for QuantLib / cvxpy / lxml builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m spacy download en_core_web_sm || true

COPY . .

# Managed hosts inject the port to bind on via $PORT; default to 8501 locally.
ENV PORT=8501
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.getenv('PORT','8501'); sys.exit(0 if urllib.request.urlopen(f'http://localhost:{p}/_stcore/health').status==200 else 1)" || exit 1

# Shell form so ${PORT} expands. Bind 0.0.0.0 so the host proxy can reach it.
CMD streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}

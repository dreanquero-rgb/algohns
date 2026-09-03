# Algohns V12 — container image for the Streamlit dashboard.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501

WORKDIR /app

# System deps for QuantLib / cvxpy / lxml builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m spacy download en_core_web_sm || true

COPY . .

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)" || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]

# Kept for reference only. The Synology DS218play is a Realtek RTD1296, and
# Synology does not ship Container Manager for Realtek platforms, so Docker is
# not available there - see DEPLOY.md. This becomes usable if the dashboard
# ever moves to a Raspberry Pi or any ordinary Linux box.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The database lives on a mounted volume so it survives image rebuilds.
ENV PW_DB_PATH=/data/wellness.db \
    PW_EXPORT_DIR=/data/exports \
    PYTHONUNBUFFERED=1

EXPOSE 8503

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8503/_stcore/health')"

CMD ["streamlit", "run", "app.py", \
     "--server.port=8503", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]

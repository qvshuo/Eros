FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 1000 eros && mkdir -p /data /media && chown eros:eros /data
COPY --chown=1000:1000 eros_scraper ./eros_scraper
USER eros
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9307/api/health', timeout=5)"]
CMD ["python", "-m", "eros_scraper.container"]

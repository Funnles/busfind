FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUSFIND_PORT=8000

RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin busfind

WORKDIR /app

COPY server.py ./
COPY fixtures/ ./fixtures/

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"

CMD ["python3", "server.py"]

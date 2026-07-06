FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects $PORT; gunicorn must bind to it. Single worker keeps the
# in-memory schedule/Letterboxd caches from fragmenting across processes;
# threads let /api/status progress polls (and other requests) be served WHILE
# a 30-60s schedule rebuild holds one thread -- with the default sync worker,
# everything queued behind the build and the loading UI froze.
CMD exec gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 300

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects $PORT; gunicorn must bind to it. Single worker keeps the
# in-memory schedule/Letterboxd caches from fragmenting across processes.
CMD exec gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --timeout 300

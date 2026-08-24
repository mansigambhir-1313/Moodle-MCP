FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -u 10001 -m appuser
USER appuser

# shell form so $PORT expands at container start
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}

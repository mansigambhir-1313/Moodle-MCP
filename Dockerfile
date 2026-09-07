FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -u 10001 -m appuser
USER appuser

# The shell expands Render's $PORT; exec makes uvicorn PID 1 so deploy signals
# reach it directly for a graceful shutdown.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]

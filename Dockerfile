FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .
ENV DATA_DIR=/tmp/var
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "mkdir -p /tmp/var && cp -n /app/data/demo/latin.db /tmp/var/ && cp -Rn /app/data/demo/images /tmp/var/ 2>/dev/null; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

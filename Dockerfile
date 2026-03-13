# Stage 1: Builder - installs dependencies and copies app
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies (if any), e.g. gcc for wheels, but let's keep it minimal here
# RUN apt-get update && apt-get install -y build-essential

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Stage 2: Runtime - minimal image with only installed packages and app code
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy installed packages from builder (from site-packages)
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy app code
COPY --from=builder /app /app

# Expose port for FastAPI
EXPOSE 8000

ENV PYTHONPATH=/app

# Run uvicorn with reload for development; for production remove --reload flag
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--log-config", "server/logging.ini"]

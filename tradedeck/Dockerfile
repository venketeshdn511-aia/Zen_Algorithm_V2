FROM python:3.11-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Use a virtualenv to encapsulate dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.11-slim AS runtime

# Security: non-root user
RUN groupadd -r trader && useradd -r -g trader -d /app trader
WORKDIR /app

# Copy the virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

COPY --chown=trader:trader . .

USER trader
# Use the virtualenv's binaries
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Matplotlib Config Dir
ENV MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8000

# Gunicorn with Uvicorn workers
# We call it via the full path or ensure /opt/venv/bin is in PATH
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "1", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "10", \
     "--keep-alive", "5", \
     "--log-level", "info", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--capture-output"]

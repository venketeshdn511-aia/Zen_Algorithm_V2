# -------------------------
# STAGE 1: Build Frontend
# -------------------------
FROM node:18-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# -------------------------
# STAGE 2: Build Backend
# -------------------------
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Security: non-root user
RUN groupadd -r trader && useradd -r -g trader -d /app trader

WORKDIR /app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy Python requirements first for caching
COPY tradedeck/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install "uvicorn[standard]" gunicorn "fastapi[all]"

# Copy backend source with correct ownership
COPY --chown=trader:trader tradedeck/ ./

# Copy compiled frontend from Stage 1
COPY --from=frontend-builder --chown=trader:trader /app/frontend/dist /app/frontend_dist

# Set permissions for the app directory
RUN chown -R trader:trader /app

USER trader

# Expose Render's default port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0
ENV SERVE_FRONTEND=true
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Start via Gunicorn
CMD ["gunicorn", "app.main:app", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--timeout", "120"]

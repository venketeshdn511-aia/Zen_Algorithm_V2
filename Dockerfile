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

# Install system dependencies required for building Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python requirements first for caching
COPY tradedeck/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install "uvicorn[standard]" gunicorn "fastapi[all]"

# Copy backend source
COPY tradedeck/ ./

# Copy compiled frontend from Stage 1 into the static delivery folder of FastAPI
COPY --from=frontend-builder /app/frontend/dist /app/frontend_dist

# Expose Render's default port
EXPOSE 8000
ENV PORT=8000
ENV HOST=0.0.0.0
# Set environment hint so FastAPI knows to serve React static files
ENV SERVE_FRONTEND=true

# Start via Gunicorn with Uvicorn worker for async capabilities
CMD ["gunicorn", "app.main:app", "--workers", "2", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--timeout", "120"]

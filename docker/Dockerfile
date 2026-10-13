# Build from the repo root: docker build -f docker/Dockerfile -t chaos-gate-orchestrator:latest .

# --- stage 1: build the React dashboard -------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- stage 2: the orchestrator, serving the built dashboard ------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# dependencies in their own layer for cache efficiency
COPY orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/ .
COPY --from=ui /ui/dist ./static

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin orchestrator \
    && mkdir /reports && chown orchestrator /reports
USER 10001

CMD ["python", "main.py"]

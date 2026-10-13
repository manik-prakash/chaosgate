# docker build -t demo-backend:v1 --build-arg VERSION=v1 demo-app
# docker build -t demo-backend:v3-bad --build-arg VERSION=v3-bad --build-arg ERROR_RATE=0.6 demo-app
FROM python:3.11-slim
ARG VERSION=dev
ARG ERROR_RATE=0
ARG STARTUP_DELAY=0
ENV VERSION=$VERSION ERROR_RATE=$ERROR_RATE STARTUP_DELAY=$STARTUP_DELAY PYTHONUNBUFFERED=1
WORKDIR /app
COPY app.py loadgen.py ./
RUN useradd --uid 10001 --no-create-home app
USER 10001
EXPOSE 8080
CMD ["python", "app.py"]

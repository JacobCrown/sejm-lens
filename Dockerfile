FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Collect static files for WhiteNoise at build time (no database needed).
# The dummy key only exists for this step; the real SECRET_KEY is provided at runtime.
RUN SECRET_KEY=collectstatic-only DEBUG=false python manage.py collectstatic --noinput

# Free Render web services have no pre-deploy hook, so migrations run on boot.
# Render injects $PORT; it defaults to 8642 for local Docker runs.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8642} --workers ${GUNICORN_WORKERS:-2} --timeout 120 --access-logfile -"]

EXPOSE 8642

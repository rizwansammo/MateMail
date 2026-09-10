# MateMail backend image.
#
# Built in CI and published to GHCR as
#   ghcr.io/rizwansammo/matemail-backend:<commit-sha>
# Never built on the production server.
#
# The same image runs the web process, the Celery worker and Celery beat; the
# command differs per service.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is required by the container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time so the image is self-contained and the
# server never needs to run a management command to become servable.
#
# prod settings require DJANGO_SECRET_KEY, so a throwaway value is supplied for
# this step only. It is not baked into the image: collectstatic neither reads
# nor writes the key, and no ENV/ARG persists it. The real key arrives at
# runtime from the environment.
#
# There is deliberately no `|| true` here. This step failed silently before,
# leaving CompressedManifestStaticFilesStorage without a manifest, which makes
# every static reference raise at runtime. A broken build must fail the build.
RUN DJANGO_SECRET_KEY="build-time-only-not-a-runtime-secret-000000000000" \
    DATABASE_URL="sqlite:///./build.sqlite3" \
    python manage.py collectstatic --noinput --settings=config.settings.prod \
    && rm -f build.sqlite3

# Run as a non-root user in production.
RUN useradd --system --create-home --uid 10001 matemail \
    && chown -R matemail:matemail /app
USER matemail

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health/ || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]

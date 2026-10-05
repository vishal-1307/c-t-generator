# Backend container: FastAPI + OR-Tools CP-SAT.
#
# Deliberately a container rather than a platform-specific buildpack. The
# backend has two requirements that rule out serverless hosting entirely, and
# a container is the portable way to satisfy both:
#
#   1. **OR-Tools** is a large native dependency. It installs cleanly from a
#      wheel on manylinux, but it is not something to leave to a host's
#      dependency detection.
#   2. **Generation runs in a background thread** and can take tens of seconds
#      (see app/jobs.py). The request returns immediately with a run id and the
#      client polls. That needs a *persistent process* - a serverless function
#      is frozen or killed once its response is sent, which would strand every
#      solve halfway through.
#
# Any host that runs a container with a long-lived process works: Render,
# Railway, Fly.io, Cloud Run (with CPU always allocated), a VM, or plain
# `docker run`. See DEPLOYMENT.md.

FROM python:3.12-slim

# Build tooling is not needed at runtime; every dependency here ships a wheel.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not re-download OR-Tools.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. The application never writes to disk - uploads are
# parsed in memory and stored in the database - so nothing here needs a
# writable volume.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrations run before the server starts, so a deployment can never serve
# against a schema it does not have. `upgrade head` is idempotent: on an
# unchanged database it applies nothing.
#
# One worker, deliberately. Generation is CPU-bound CP-SAT with a
# process-global single-solve guard (app/jobs.py), so extra workers would not
# add generation throughput - they would only let two solves start at once on
# the same box and compete for the same cores. Scale by making the instance
# bigger, or run a second instance behind the load balancer for read traffic.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 65"]

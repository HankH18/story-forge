# syntax=docker/dockerfile:1.9
#
# Story Forge API — production image (§16 phase 7, §3 stack).
#
# BUILD CONTEXT IS THE REPO ROOT, not services/api:
#
#     docker build -f services/api/Dockerfile -t storyforge-api .
#
# Two things force that. `storyforge.seed` and `storyforge.precompute` resolve
# their data as `Path(__file__).resolve().parents[3] / "data"`, so the image has
# to reproduce the repo's directory shape rather than flatten it; and
# `data/seed/gpcs.csv` is a *relative* symlink into the decoder package
# (DEVIATIONS 1.4) which only resolves when both trees are present under the
# same root. Hence /app/data and /app/services/api, laid out exactly as the
# checkout is.
#
# This image NEVER runs migrations at start. N instances starting together would
# race each other through `alembic upgrade head`; Alembic runs exactly once, in
# the PRE_DEPLOY job in .do/app.yaml.
#
# No secret is baked in. `services/api/.env` is excluded by the root
# .dockerignore and nothing here COPYs it; every production value arrives as a
# platform SECRET env var at run time.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.8

# uv is pinned to a version, not `:latest`. DEVIATIONS 0.2 puts the interpreter
# and the lockfile inside the reproducibility claim the validator's proof rests
# on; a resolver that drifts underneath a pinned lock is the same class of hole.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv


# --- builder: resolve dependencies from the committed lock -------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

WORKDIR /app/services/api

# Manifest and lock only, so the dependency layer is cached against source
# changes. Copying the package here would invalidate it on every edit.
COPY services/api/pyproject.toml services/api/uv.lock ./

# `--frozen` is the reproducibility guarantee: uv fails if uv.lock does not
# match pyproject.toml instead of quietly re-resolving to newer versions, so the
# image gets the exact dependency set the test suite was green against. Never
# replace this with a loose `pip install`.
#
# `--no-dev` drops pytest, hypothesis, ruff, cmudict and wordfreq — the last two
# are vendoring-script tools, not runtime deps (the decoder reads the pinned
# cmudict.dict snapshot from package data, precompute reads data/frequency-en.csv).
#
# `--no-install-project` deliberately leaves `storyforge` OUT of site-packages.
# It is put on PYTHONPATH in the runtime stage instead, so `__file__` is
# `/app/services/api/storyforge/...` and the `parents[3]` arithmetic above
# resolves to `/app/data`. An installed (non-editable) copy would land in
# site-packages and silently break `make seed`.
RUN uv sync --frozen --no-dev --no-install-project


# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="storyforge-api" \
      org.opencontainers.image.description="Story Forge API — decodable story generation" \
      org.opencontainers.image.source="https://labs.gauntletai.com/hankholcomb/story-forge"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root. uid/gid are fixed rather than auto-assigned so a mounted volume's
# ownership is predictable across rebuilds.
RUN groupadd --system --gid 10001 storyforge \
 && useradd --system --uid 10001 --gid storyforge --home-dir /app --shell /usr/sbin/nologin storyforge

WORKDIR /app/services/api

# The venv carries absolute paths in pyvenv.cfg and its console scripts, so it
# must land on the same path it was built at.
COPY --from=builder --chown=storyforge:storyforge /app/services/api/.venv /app/services/api/.venv

# Seed CSVs, the blocklist and the pinned frequency list. `data/seed/gpcs.csv`
# is a relative symlink into the decoder package; it is copied as a symlink and
# resolves because the next COPY puts its target under the same /app root.
COPY --chown=storyforge:storyforge data /app/data

COPY --chown=storyforge:storyforge services/api/alembic.ini services/api/pyproject.toml ./
COPY --chown=storyforge:storyforge services/api/storyforge ./storyforge

ENV VIRTUAL_ENV=/app/services/api/.venv \
    PATH=/app/services/api/.venv/bin:$PATH \
    PYTHONPATH=/app/services/api

USER storyforge

# DO App Platform injects PORT and expects the process to honour it. 8080 is the
# platform default and the local default, so `docker run -p 8080:8080` works.
ENV PORT=8080
EXPOSE 8080

# /api/health returns HTTP 200 even when a component is broken — the body
# carries the verdict (`status: ok|pending|error`). A status-code-only check
# would call a database outage healthy, so this one reads the body. DO's own
# health check cannot do that; see docs/DEPLOY.md.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import json,os,urllib.request;r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/health',timeout=4);d=json.load(r);raise SystemExit(0 if r.status==200 and d.get('status')!='error' else 1)"]

# One worker per instance, on purpose. `stories.limiter` is in-process
# (DEVIATIONS 4.10 / 6.x), so §9's "10 story requests per minute" becomes
# 10 x workers x instances. Until a shared counter exists, the honest
# configuration is a single worker and instance_count: 1 in .do/app.yaml.
#
# --proxy-headers + --forwarded-allow-ips: DO terminates TLS at its router and
# forwards X-Forwarded-For/-Proto. Without these, every client address logged is
# the router's and every generated URL claims http://.
#
# `exec` so uvicorn is PID 1 and receives SIGTERM directly — otherwise the
# shell swallows it and the platform's graceful-shutdown window is wasted.
CMD ["sh", "-c", "exec uvicorn storyforge.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]

# syntax=docker/dockerfile:1.7
#
# beWithMe service image.
#
# Every one of the 8 sidecars is `uvicorn services.<name>.main:app` over the
# SAME source tree (infra/topology.py:SERVICE_OFFSETS). So we do NOT build 8
# images -- we build two, and each container picks its service via its command:
#
#   docker run ... bewithme-core  uvicorn services.shell.main:app --port 8000
#
# Two targets, because the split is system dependencies, not Python code:
#
#   core   -> shell, persona, knowledge, maestro, tuning   (slim)
#   media  -> transcribe, speak, browser                   (+ffmpeg, +chromium)
#
# Model artifacts (Whisper .bin, Kokoro onnx, EOU onnx) are deliberately NOT
# baked in: they are large, licensed separately, and fetched by
# scripts/fetch_eou_model.sh. Mount them at /models and point
# WHISPER_MODEL_PATH / KOKORO_* / EOU_MODEL_PATH at that mount.

# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build-only toolchain for wheels that compile (pywhispercpp, webrtcvad, nh3).
# Installed here and NOT carried into the runtime layers below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer -- cached until requirements.txt actually changes.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt

# =============================================================================
# core -- the five pure-Python sidecars.
#   shell (+0)  persona (+1)  knowledge (+2)  maestro (+6)  tuning (+8)
# =============================================================================
FROM base AS core

# Drop the build toolchain; keep only what the app needs at runtime.
RUN apt-get purge -y build-essential cmake git && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser . /app

# data/ holds per-user session + canvas state (see infra/user_data.py). It must
# be a mounted volume in any real deploy, or state dies with the container.
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data
VOLUME ["/app/data"]

USER appuser

# No CMD: the orchestrator supplies the service. Fail loudly if it forgets.
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["--help"]

# =============================================================================
# media -- the three sidecars with native/system dependencies.
#   transcribe (+3, Whisper + EOU onnx)   speak (+4, Kokoro)   browser (+5, Playwright)
# =============================================================================
FROM base AS media

# ffmpeg/ffprobe: hard prerequisite for services/transcribe and infra/media
# (CLAUDE.md > Prerequisites). Chromium deps come via `playwright install`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Chromium for services/browser (Playwright) and infra/tools/web_fetch.py.
# --with-deps pulls the shared libraries Chromium needs on slim.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser . /app

# /models: mount Whisper + Kokoro + EOU artifacts here (never baked in).
# /app/data: per-user state, same as core.
RUN mkdir -p /app/data /models && \
    chown -R appuser:appuser /app/data /models && \
    chmod -R a+rX /opt/playwright
VOLUME ["/app/data", "/models"]

USER appuser

ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["--help"]

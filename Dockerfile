ARG PYTHON_IMAGE=python:3.13.14-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        libheif-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libheif1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 carwrap \
    && useradd --uid 10001 --gid carwrap --no-create-home --shell /usr/sbin/nologin carwrap

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY frontend ./frontend
COPY src ./src

USER 10001:10001

CMD ["uvicorn", "car_wrap.runtime:build_application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

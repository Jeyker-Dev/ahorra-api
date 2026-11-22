FROM python:3.13-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

ARG SUPERCRONIC_VERSION=v0.2.51

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && arch="$(dpkg --print-architecture)" \
    && case "${arch}" in \
         amd64) sha256="cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123" ;; \
         arm64) sha256="4ae22576a78223d8ccdf5f3660a5cbb5288416bf0ca96afc4a36d195932b94" ;; \
         *) echo "Unsupported architecture: ${arch}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${arch}" -o /usr/local/bin/supercronic \
    && echo "${sha256}  /usr/local/bin/supercronic" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/supercronic

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.13-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home app

WORKDIR /app

COPY --from=builder /usr/local/bin/supercronic /usr/local/bin/supercronic
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app docker ./docker
COPY --chown=app:app src ./src

ENV PATH="/app/.venv/bin:$PATH"

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

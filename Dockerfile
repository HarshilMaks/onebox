# Pin patch releases and update them only through a reviewed dependency/image update.
FROM python:3.12.8-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /build
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt


FROM python:3.12.8-slim-bookworm AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 onebox \
    && useradd --uid 10001 --gid onebox --create-home --home-dir /app onebox

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=onebox:onebox server ./server
COPY --chown=onebox:onebox clients ./clients
COPY --chown=onebox:onebox tools ./tools
COPY --chown=onebox:onebox alembic ./alembic
COPY --chown=onebox:onebox agents.py alembic.ini user_config.yaml ./

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]

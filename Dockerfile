# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.lock
RUN groupadd --gid 10001 gallery && useradd --uid 10001 --gid gallery --no-create-home gallery \
    && mkdir /data /storage && chown gallery:gallery /data /storage
COPY --chown=gallery:gallery app.py bootstrap.py manage.py ./
COPY --chown=gallery:gallery static ./static
COPY docker-entrypoint.sh ./
RUN chmod 755 docker-entrypoint.sh
USER gallery
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "--workers", "2", "--threads", "2", "--timeout", "120", "--bind", "0.0.0.0:8000", "app:create_app()"]

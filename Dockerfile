FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -m bot && mkdir -p state logs data reports && chown -R bot /app
USER bot
ENTRYPOINT ["orb", "-c", "/app/config.yaml"]
CMD ["paper"]

FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.6 \
    && useradd --uid 10001 --create-home app
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project --default-index https://pypi.org/simple
COPY open_learning ./open_learning
COPY migrations ./migrations
COPY alembic.ini ./
USER 10001
EXPOSE 8000
CMD [".venv/bin/uvicorn", "open_learning.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

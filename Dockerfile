FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY app/ ./app/

RUN pip install --no-cache-dir .

ENV PORT=8080

EXPOSE 8080

CMD ["python", "-m", "app.fast_api_app"]

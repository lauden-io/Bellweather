FROM python:3.12-alpine3.20
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY app /app
WORKDIR /app
CMD ["python", "-u", "/app/runner.py"]

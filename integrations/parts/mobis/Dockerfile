FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MOBIS_CONFIG=/run/config/mobis.ini
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 10001 --create-home parts
COPY mobis_api ./mobis_api
USER parts
EXPOSE 8000
CMD ["uvicorn", "mobis_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "24", "--no-access-log"]

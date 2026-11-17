FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 EPC_CONFIG=/run/config/renault.ini
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 10001 --create-home parts
COPY epc_api ./epc_api
USER parts
EXPOSE 8000
CMD ["uvicorn", "epc_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "12", "--no-access-log"]

FROM python:3.12-slim
WORKDIR /app

COPY python/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY python/app ./app
COPY frontend/public ./frontend/public

ENV PYTHONPATH=/app
ENV APP_BASE_URL=http://127.0.0.1:8080
ENV DATABASE_PATH=/tmp/sharecontacts.db
RUN mkdir -p /tmp

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

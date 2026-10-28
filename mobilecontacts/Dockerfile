FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY mobilecontacts/python/requirements-cloud.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY db ./db
COPY mobilecontacts/python/mobile ./mobilecontacts/python/mobile

ENV PYTHONPATH=/srv:/srv/mobilecontacts/python
ENV CFS_PROJECT_ROOT=/srv
ENV DATA_ROOT=/data
ENV PORT=8080

RUN mkdir -p /data

WORKDIR /srv/mobilecontacts/python
CMD ["uvicorn", "mobile.main:app", "--host", "0.0.0.0", "--port", "8080"]

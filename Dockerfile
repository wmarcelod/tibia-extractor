FROM python:3.12-slim

ENV TZ=Europe/Berlin \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata cron curl ca-certificates \
 && ln -sf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY docker/crontab /etc/cron.d/tibia-cron
RUN chmod 0644 /etc/cron.d/tibia-cron \
 && crontab /etc/cron.d/tibia-cron

RUN chmod +x /app/docker/entrypoint.sh

EXPOSE 5000

CMD ["/app/docker/entrypoint.sh"]

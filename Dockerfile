FROM python:3.12-slim

RUN apt-get update && apt-get install -y nginx gettext-base \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py monitor.py set_target.sh ./
COPY nginx.conf.template /etc/nginx/nginx.conf.template

RUN chmod +x /app/set_target.sh

ENV PORT=10000
EXPOSE 10000

COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]

FROM ghcr.io/home-assistant/amd64-base:latest

RUN apk add --no-cache \
    python3 \
    py3-pip

RUN pip install \
    playwright \
    playwright-stealth \
    paho-mqtt \
    requests \
    python-dotenv

RUN playwright install chromium

COPY app /app
COPY socalgas/run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
FROM ghcr.io/home-assistant/amd64-base:latest

RUN apk add --no-cache \
    python3 \
    py3-pip

RUN pip install \
    playwright \
    playwright-stealth \
    paho-mqtt \
    requests

RUN playwright install chromium

COPY rootfs /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
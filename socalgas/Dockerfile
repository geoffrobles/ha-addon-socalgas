FROM mcr.microsoft.com/playwright/python:latest

RUN pip install \
    playwright-stealth \
    paho-mqtt \
    requests \
    python-dotenv

COPY app /app
COPY socalgas/run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
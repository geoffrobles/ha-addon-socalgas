FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

RUN pip install \
    playwright-stealth \
    paho-mqtt \
    requests \
    python-dotenv

COPY socalgas_api_slim.py /app/
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
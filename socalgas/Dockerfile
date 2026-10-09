FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY socalgas_api_slim.py /app/
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]

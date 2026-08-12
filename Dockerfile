FROM python:3.11-slim

WORKDIR /app

# Install system deps (if any) and pip requirements
COPY requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y build-essential gcc \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

EXPOSE 8006

ENV PORT=8006 \
    TRADE_AMOUNT_USDT=50.0 \
    MAX_TRADE_RISK_USDT=2.0

CMD ["bash", "start.sh"]

FROM python:3.13-slim

# Install system dependencies & Chromium browser for Selenium
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    libxi6 \
    libgconf-2-4 \
    libnss3 \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]

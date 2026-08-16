FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN mkdir -p /app/videos /app/fonts
CMD gunicorn app_v7:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1

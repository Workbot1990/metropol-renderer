FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg wget unzip fontconfig \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/fonts && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/static/PlayfairDisplay-Regular.ttf" \
    -O /app/fonts/PlayfairDisplay-Regular.ttf && \
    wget -q "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/static/PlayfairDisplay-Bold.ttf" \
    -O /app/fonts/PlayfairDisplay-Bold.ttf

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

RUN mkdir -p /app/videos

CMD gunicorn app:app

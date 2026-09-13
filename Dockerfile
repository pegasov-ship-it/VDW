FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno - recommended JavaScript runtime for yt-dlp YouTube extraction.
RUN curl -fsSL https://deno.land/install.sh | sh \
    && ln -s /root/.deno/bin/deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir \
    "yt-dlp[default]" \
    aiogram \
    cryptography \
    asyncpg

COPY . .

CMD ["python", "bot.py"]

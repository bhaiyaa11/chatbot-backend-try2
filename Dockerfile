# FROM python:3.10-slim

# WORKDIR /app

# COPY requirements.txt .

# RUN pip install --no-cache-dir -r requirements.txt

# COPY . .
# COPY service-account.json /app/service-account.json

# CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.10-slim

WORKDIR /app

# ✅ System deps (CRITICAL for yt-dlp + whisper)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ✅ Prevent Python buffering (better logs)
ENV PYTHONUNBUFFERED=1

# ✅ Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ✅ Copy app
COPY . .

# ✅ Cloud Run requires PORT env
ENV PORT=8080

# ✅ Start server
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8080"]
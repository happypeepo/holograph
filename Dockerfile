# No system packages needed: the app is pure Python since the ffmpeg video path
# was removed. Slim base keeps the image small enough to redeploy in seconds.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Platforms inject $PORT; main.py does not read it, so bind it here.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

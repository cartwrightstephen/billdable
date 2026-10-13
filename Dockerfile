FROM python:3.12.7-slim
# age encrypts backups before they are uploaded off the server (offsite.py).
RUN apt-get update && apt-get install -y --no-install-recommends age && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
EXPOSE 8765
CMD ["python3", "server.py"]

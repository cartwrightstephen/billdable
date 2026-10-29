FROM python:3.12.7-slim
# age encrypts backups before they are uploaded off the server (offsite.py).
RUN apt-get update && apt-get install -y --no-install-recommends age && rm -rf /var/lib/apt/lists/*
# The server runs as duracash, not root (R6-09). The code stays root's and read-only to it;
# only the data directory is its own. /app/data is the default; Render mounts /var/data.
RUN groupadd --system duracash && useradd --system --gid duracash --home-dir /app --no-create-home duracash
WORKDIR /app
COPY . .
RUN mkdir -p /app/data /var/data && chown duracash:duracash /app/data /var/data && chmod 755 scripts/docker-start.sh
EXPOSE 8765
# Starts as root only to hand a freshly mounted disk to duracash, then runs the server as duracash.
CMD ["scripts/docker-start.sh"]

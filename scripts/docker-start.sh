#!/bin/sh
# The container's start command (Dockerfile, R6-09). A Render disk is mounted owned by root, so
# this runs as root just long enough to give the data directory to the duracash user, then runs
# the server as duracash. Started as any other user, it runs the server as that user.
set -e
dir="${DURACASH_DATA_DIR:-/app/data}"
mkdir -p "$dir"
if [ "$(id -u)" = "0" ]; then
    # Files an admin.py command run as root left behind are handed over too.
    chown -R duracash:duracash "$dir"
    exec setpriv --reuid=duracash --regid=duracash --init-groups python3 server.py
fi
exec python3 server.py

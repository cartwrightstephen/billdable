FROM python:3.12.7-slim
WORKDIR /app
COPY . .
EXPOSE 8765
CMD ["python3", "server.py"]

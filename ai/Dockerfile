FROM python:3.10-slim

WORKDIR /app

COPY ./ai /app

RUN pip3 install --no-cache-dir --break-system-packages fastapi uvicorn sentence-transformers docker requests

EXPOSE 8000

CMD ["python", "main.py"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8000

WORKDIR /app

COPY app.py README.md ./
COPY codebase_rag ./codebase_rag
COPY static ./static

RUN mkdir -p /app/.rag_demo/repos /app/.rag_demo/indexes

EXPOSE 8000

CMD ["python", "app.py"]

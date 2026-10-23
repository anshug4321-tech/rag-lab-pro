FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 EMBED_BACKEND=gemini
# Hugging Face runs containers as user 1000, so create it and let it write to /app/data
RUN useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
RUN mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 7860
# Render sets $PORT; Hugging Face Spaces uses 7860
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]

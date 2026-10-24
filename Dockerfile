FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 EMBED_BACKEND=gemini
RUN id -u app >/dev/null 2>&1 || useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
RUN if [ -d Backend ] && [ ! -d backend ]; then mv Backend backend; fi \
 && if [ -d Frontend ] && [ ! -d frontend ]; then mv Frontend frontend; fi \
 && if [ -d backend/Route ] && [ ! -d backend/routers ]; then mv backend/Route backend/routers; fi
RUN mkdir -p /app/data && chown -R app:app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]

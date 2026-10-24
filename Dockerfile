FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 EMBED_BACKEND=gemini
RUN useradd -m -u 1000 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
# GitHub upload me folder ke naam ke capital letters theek karo (Linux case-sensitive hai)
RUN if [ -d Backend ] && [ ! -d backend ]; then mv Backend backend; fi \
 && if [ -d Frontend ] && [ ! -d frontend ]; then mv Frontend frontend; fi \
 && if [ -d backend/Route ] && [ ! -d backend/routers ]; then mv backend/Route backend/routers; fi \
 && if [ -d backend/Routers ] && [ ! -d backend/routers ]; then mv backend/Routers backend/routers; fi
# Build log me folder structure dikhao (debugging ke liye)
RUN find . -maxdepth 3 -not -path "./.git*" | sort | head -80
RUN mkdir -p /app/data && chown -R app:app /app
USER app
EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]FROM python:3.11-slim
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

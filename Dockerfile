# Strip Reader in a container: Render (render.yaml), Hugging Face Spaces, or any Docker host.
FROM python:3.13-slim

# OpenCV (used by the offline text reader) needs these system libraries.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# Public-demo settings (secrets such as GEMINI_API_KEY are set in the host's settings, never here).
ENV STRIP_READER_RUNS=/home/user/app/out/app \
    STRIP_READER_INSTANCE=/home/user/app/instance \
    RETENTION_HOURS=24 \
    DAILY_LIMIT=10 \
    BEHIND_PROXY=1 \
    PORT=7860

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/healthz').status==200 else 1)"

# One worker (the visitor limit and sign-in lockout live in memory), threads for parallel visitors,
# a long timeout because one AI reading can take up to a minute.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:${PORT} 'strip_reader.app:wsgi()'"]

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && mkdir -p /certs \
    && openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
      -keyout /certs/sapbo-mock.key \
      -out /certs/sapbo-mock.crt \
      -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    && apt-get purge -y --auto-remove openssl \
    && rm -rf /var/lib/apt/lists/*

COPY server.py /app/server.py

EXPOSE 8443

HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import ssl, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen('https://127.0.0.1:8443/health', timeout=2, context=ctx).read()"

CMD ["python", "/app/server.py"]

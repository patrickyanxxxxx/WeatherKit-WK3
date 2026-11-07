FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-numpy python3-pil ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /var/lib/radar-demo --shell /usr/sbin/nologin radar-demo \
    && mkdir -p /app /var/lib/radar-demo/source /var/lib/radar-demo/native \
    && chown -R radar-demo:radar-demo /var/lib/radar-demo
WORKDIR /app
COPY *.py palette.csv ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER radar-demo
EXPOSE 8768
ENTRYPOINT ["python3", "/app/native_gateway.py"]
CMD ["--host", "0.0.0.0", "--port", "8768", "--cache", "/var/lib/radar-demo/native", "--source-cache", "/var/lib/radar-demo/source"]

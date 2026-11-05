FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY boersen_agent ./boersen_agent
COPY docs ./docs
COPY tools ./tools
# State, reports and config live in the /data volume; paths in config.toml are relative to it.
VOLUME /data
ENTRYPOINT ["python", "-m", "boersen_agent", "--config", "/data/config.toml"]
CMD ["run", "--json"]

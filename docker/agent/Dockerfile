# capital-agent scheduler + driver, plus the upstream capital-mcp
# pinned via UPSTREAM_REF (same tag docker/capital-mcp/Dockerfile uses).
# Runs `python -m capital_agent run` as the entrypoint; the scheduler
# then spawns capital-mcp as a stdio subprocess just like on Windows.

FROM python:3.12-slim AS build

ARG UPSTREAM_REF=main
ARG UPSTREAM_REPO=https://github.com/capital-com-sv/capital-mcp.git

RUN apt-get update && apt-get install -y --no-install-recommends \
    git nodejs npm && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally so `claude` is on PATH.
RUN npm install -g "@anthropic-ai/claude-code"

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY prompts ./prompts
COPY config ./config

# Install our package + upstream capital-mcp from git.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        "git+${UPSTREAM_REPO}@${UPSTREAM_REF}" \
        -e .

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /app /app
WORKDIR /app

RUN useradd -m -u 10001 agent && chown -R agent:agent /app
USER agent

# State lives on a mounted volume so restarts preserve equity_snapshots,
# positions_local, signals, and the kill switch.
VOLUME ["/app/state", "/app/logs"]

# Health API on 8080 for the outside world.
EXPOSE 8080

ENTRYPOINT ["python", "-m", "capital_agent", "run"]

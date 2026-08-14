# Build the upstream Capital.com MCP server from a pinned ref.
# Do NOT modify upstream source. Track updates by bumping UPSTREAM_REF.
FROM python:3.12-slim AS build

ARG UPSTREAM_REF=main
ARG UPSTREAM_REPO=https://github.com/capital-com-sv/capital-mcp.git

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${UPSTREAM_REF}" "${UPSTREAM_REPO}" capital-mcp \
    || (git clone "${UPSTREAM_REPO}" capital-mcp && cd capital-mcp && git checkout "${UPSTREAM_REF}")

WORKDIR /src/capital-mcp
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

FROM python:3.12-slim
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

# Non-root
RUN useradd -m -u 10001 mcp
USER mcp
WORKDIR /home/mcp

# The server reads CAP_* from env. It speaks MCP over stdio; there's no
# port to expose. Keep container alive so `docker exec` can drive it.
CMD ["sh", "-c", "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"]

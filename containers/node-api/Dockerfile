FROM rust:1.88-bookworm AS tracker-builder
WORKDIR /build
COPY tracker-mcp-server/Cargo.toml tracker-mcp-server/Cargo.lock ./tracker-mcp-server/
COPY tracker-mcp-server/src ./tracker-mcp-server/src
WORKDIR /build/tracker-mcp-server
RUN cargo build --release

FROM node:22-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    git \
    jq \
    patch \
    python3 \
    python3-pip \
    ripgrep \
    zsh \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/local/bin/python

RUN corepack enable && \
    corepack prepare pnpm@10.16.0 --activate && \
    corepack prepare yarn@1.22.22 --activate

RUN npm install -g @openai/codex @anthropic-ai/claude-code

COPY --from=tracker-builder /build/tracker-mcp-server/target/release/tracker-mcp-server /usr/local/bin/tracker-mcp-server

RUN useradd -m -s /bin/bash agent && \
    mkdir -p /workspace /run/agent && \
    chown -R agent:agent /workspace /run/agent /home/agent

USER agent
WORKDIR /workspace
ENV HOME=/home/agent

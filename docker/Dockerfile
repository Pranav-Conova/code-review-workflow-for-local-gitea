FROM node:20-slim

# Install Python, Go, and essentials
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Go
RUN curl -fsSL https://go.dev/dl/go1.24.1.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:/root/go/bin:${PATH}"
ENV GOPATH="/root/go"
ENV GOMODCACHE="/root/go/pkg/mod"

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Pre-download the Gitea MCP module so first review is fast
RUN go install gitea.com/gitea/gitea-mcp@latest

# Create non-root user (Claude CLI refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash reviewer \
    && cp -r /root/go /home/reviewer/go \
    && chown -R reviewer:reviewer /home/reviewer/go

# Set up project directory
WORKDIR /app

# Copy project files (build context is project root: docker build -f docker/Dockerfile .)
COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

COPY .mcp.json .mcp.json
COPY src/ src/

# Create a container-specific .mcp.json that uses system Go
RUN echo '{\n\
  "mcpServers": {\n\
    "gitea": {\n\
      "command": "/usr/local/go/bin/go",\n\
      "args": ["run", "gitea.com/gitea/gitea-mcp@latest", "-t", "stdio"],\n\
      "env": {\n\
        "GITEA_HOST": "__GITEA_HOST__",\n\
        "GITEA_ACCESS_TOKEN": "__GITEA_TOKEN__",\n\
        "GOPATH": "/home/reviewer/go",\n\
        "GOMODCACHE": "/home/reviewer/go/pkg/mod"\n\
      }\n\
    }\n\
  }\n\
}' > /app/.mcp.json.template

# Create data directory for logs and state
RUN mkdir -p /app/data \
    && chown -R reviewer:reviewer /app

# Expose dashboard port
EXPOSE ${DASHBOARD_PORT:-8000}

# Entrypoint script that sets up auth + starts poller
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Volumes for persistent data
VOLUME /home/reviewer/.claude
VOLUME /app/data

ENTRYPOINT ["/entrypoint.sh"]

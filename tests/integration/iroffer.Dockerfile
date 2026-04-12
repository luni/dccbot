# Dockerfile for iroffer - official stable version from https://iroffer.net
FROM alpine:latest

# Install dependencies
RUN apk add --no-cache \
    build-base \
    curl \
    openssl-dev \
    tar

# Build iroffer from official stable tarball
WORKDIR /tmp
RUN curl -L -o iroffer.tar.gz "https://iroffer.net/iroffer-dinoex-3.34.tar.gz" && \
    tar xzf iroffer.tar.gz && \
    cd iroffer-dinoex-* && \
    ./Configure -curl -ssl && \
    make && \
    cp iroffer /usr/local/bin/ && \
    cd .. && \
    rm -rf iroffer-dinoex-* iroffer.tar.gz

# Create directories
RUN mkdir -p /files /config /config-template /tmp && chmod 777 /tmp

# Create non-root user
RUN adduser -D -u 1000 irofferuser && \
    chown -R irofferuser:irofferuser /files /config /tmp

# Copy config to template location (will be copied to /config at runtime)
COPY tests/integration/iroffer_config/mybot.config /config-template/mybot.config

# Note: Test files are generated dynamically at container startup

# Set permissions
RUN chown -R irofferuser:irofferuser /files /config /config-template

# Create startup script
RUN echo '#!/bin/sh' > /start.sh && \
    echo 'echo "Waiting for IRC server..."' >> /start.sh && \
    echo 'sleep 5' >> /start.sh && \
    echo 'echo "Starting iroffer (daemon mode)..."' >> /start.sh && \
    echo 'cd /config && iroffer -b mybot.config' >> /start.sh && \
    echo 'sleep 2' >> /start.sh && \
    echo 'exec tail -F /config/iroffer.log' >> /start.sh && \
    chmod +x /start.sh

WORKDIR /config

USER irofferuser

CMD ["/start.sh"]

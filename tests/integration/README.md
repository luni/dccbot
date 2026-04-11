# Integration Tests

These tests validate the IRC connection functionality against a real IRC server.

## Prerequisites

- Docker (with compose plugin) installed
- The Python development environment set up (`uv sync`)

## Running Tests

### Quick Start (Recommended)

Use the Makefile target which handles starting/stopping the IRC server:

```bash
uv run make test-integration-local
```

### Manual Steps

If you prefer to manage the IRC server lifecycle manually:

1. Start the IRC server:
   ```bash
   docker compose up -d ircd
   ```

2. Wait for the server to be ready (check health):
   ```bash
   make irc-up
   ```

3. Run the integration tests:
   ```bash
   uv run pytest tests/integration -v -m integration --timeout=120
   ```

4. Stop the IRC server:
   ```bash
   docker compose down
   # or
   make irc-down
   ```

## Test Configuration

The tests connect to a local InspIRCd instance with these defaults:
- **Server**: localhost
- **Plain text port**: 6667
- **TLS port**: 6697 (with auto-generated self-signed certificate)

The IRC server runs with default configuration using environment variables, with SSL certificates auto-generated on first startup.

## CI Integration

In GitHub Actions, the workflow:
1. Starts InspIRCd via docker compose
2. Waits for the server to be ready
3. Runs integration tests
4. Always shuts down the IRC server (even on failure)

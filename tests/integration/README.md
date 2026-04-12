# Integration Tests

These tests validate the IRC connection functionality against a real IRC server with an XDCC bot.

## Overview

The integration test environment consists of:
- **InspIRCd** - IRC server (ports 6667 plain, 6697 TLS with auto-generated SSL certs)
- **iroffer** - XDCC file server bot (connects as `xdccbot` in `#test` channel)
- **Test files** - Sample files served via XDCC protocol

## Current Test Coverage

### IRC Connection Tests
- `test_irc_connection` - Connect via plain (6667) and TLS (6697)
- `test_irc_connection_with_random_nick` - Random nick suffix generation
- `test_irc_channel_join` - Channel join/part functionality
- `test_irc_authentication_timeout` - Connection timeout handling

### XDCC Tests (To Be Implemented)
- Request file list from XDCC bot
- Download file via XDCC protocol
- Resume interrupted download
- Batch downloads

## Prerequisites

- Docker (with compose plugin) installed
- The Python development environment set up (`uv sync`)

## Running Tests

### Quick Start (Recommended)

Use the Makefile target which handles starting/stopping all services:

```bash
uv run make test-integration-local
```

### Manual Steps

If you prefer to manage the services manually:

1. Start all services (IRC server + XDCC bot):
   ```bash
   docker compose up -d ircd xdccbot
   ```

2. Wait for services to be ready:
   ```bash
   make irc-up
   ```

3. Run the integration tests:
   ```bash
   uv run pytest tests/integration -v -m integration --timeout=120
   ```

4. Stop all services:
   ```bash
   docker compose down
   # or
   make irc-down
   ```

## XDCC Bot Configuration

The iroffer XDCC bot (`xdccbot`) is configured to:
- Connect to IRC server at `ircd:6667`
- Join `#test` channel
- Serve files from `/files` directory
- Accept admin commands from any host (`*@*`)

### Test Files

Sample files in `test_files/` directory:
- `test1.txt` - Text file for basic download test
- `test2.txt` - Second file for batch test

### Manual XDCC Interaction

To manually test the XDCC bot:
```irc
/msg xdccbot xdcc list
/msg xdccbot xdcc send #1
```

## Test Configuration

- **Server**: localhost
- **Plain text port**: 6667
- **TLS port**: 6697 (with auto-generated self-signed certificate)
- **XDCC Bot**: xdccbot (in #test channel)

## CI Integration

In GitHub Actions, the workflow:
1. Starts InspIRCd and iroffer via docker compose
2. Waits for services to be ready
3. Runs integration tests
4. Always shuts down all services (even on failure)

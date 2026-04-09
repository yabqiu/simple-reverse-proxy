# simple-reverse-proxy

A simple HTTP reverse proxy server that logs every request and response — truncated on the console, full detail to a log file.

## Installation

```bash
uv sync
```

## Usage

```
simple-reverse-proxy <upstream> [--host HOST] [--port PORT] [--log-file PATH]
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `upstream` | *(required)* | Upstream base URL to forward requests to |
| `--host` | `127.0.0.1` | Host address to listen on |
| `--port` | `9091` | Port to listen on |
| `--log-file` | `reverse-proxy.log` | File path for full request/response logs |

### Examples

```bash
# Proxy to a local service
simple-reverse-proxy http://localhost:8080

# Listen on all interfaces, custom port
simple-reverse-proxy http://localhost:8080 --host 0.0.0.0 --port 8888

# Custom log file location
simple-reverse-proxy http://api.example.com --log-file /tmp/traffic.log
```

## Logging

**Console** — colored, truncated at 1024 characters per body:

```
────────────────────────────────────────────────────────────
REQUEST  #1  2026-04-09 21:45:33.617  127.0.0.1:55619
────────────────────────────────────────────────────────────
GET http://api.example.com/users
Host: api.example.com
User-Agent: curl/8.7.1
Accept: */*

(no body)

────────────────────────────────────────────────────────────
RESPONSE  #1  +87.0ms
────────────────────────────────────────────────────────────
200 OK
Content-Type: application/json
Content-Length: 42

[{"id":1,"name":"Alice"}]
```

**Log file** — plain text, full headers and body for every request/response pair, written to `reverse-proxy.log` by default.

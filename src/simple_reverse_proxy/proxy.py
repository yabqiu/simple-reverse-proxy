"""Simple HTTP reverse proxy server."""

import argparse
import itertools
import logging
import sys
import time
from datetime import datetime

import aiohttp
from aiohttp import web
from yarl import URL

# Headers that must not be forwarded between proxy and upstream (RFC 2616 §13.5.1)
HOP_BY_HOP = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)

CONSOLE_TRUNCATE = 1024  # max body chars shown on console

# ANSI color codes
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"


def _status_color(status: int) -> str:
    if status < 300:
        return _GREEN
    if status < 400:
        return _YELLOW
    return _RED


_sequence = itertools.count(1)
_file_logger: logging.Logger | None = None


def _filter_headers(
    headers: aiohttp.typedefs.LooseHeaders,
    extra_skip: set[str] | None = None,
) -> dict[str, str]:
    """Return a plain dict of headers with hop-by-hop and extra headers removed."""
    skip = HOP_BY_HOP | (extra_skip or set())
    return {k: v for k, v in headers.items() if k.lower() not in skip}


def _body_preview(body: bytes) -> str:
    """Return a (possibly truncated) text preview of a body."""
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return f"<binary {len(body)} bytes>"
    if len(text) <= CONSOLE_TRUNCATE:
        return text
    return text[:CONSOLE_TRUNCATE] + f" ... [truncated, total {len(body)} bytes]"



def _log_to_file(
    seq: int,
    timestamp: str,
    client_addr: str,
    req_method: str,
    req_url: str,
    req_headers: dict[str, str],
    req_body: bytes,
    resp_status: int,
    resp_reason: str,
    resp_headers: dict[str, str],
    resp_body: bytes,
    elapsed_ms: float,
) -> None:
    if _file_logger is None:
        return

    def decode(b: bytes) -> str:
        try:
            return b.decode("utf-8", errors="replace")
        except Exception:
            return f"<binary {len(b)} bytes>"

    sep = "─" * 60
    lines = [
        "",
        sep,
        f"REQUEST  #{seq}  {timestamp}  {client_addr}",
        sep,
        f"{req_method} {req_url}",
        *[f"{k}: {v}" for k, v in req_headers.items()],
        "",
        decode(req_body) if req_body else "(no body)",
        "",
        sep,
        f"RESPONSE  #{seq}  +{elapsed_ms:.1f}ms",
        sep,
        f"{resp_status} {resp_reason}",
        *[f"{k}: {v}" for k, v in resp_headers.items()],
        "",
        decode(resp_body) if resp_body else "(no body)",
        "",
    ]
    _file_logger.info("\n".join(lines))


async def handle(request: web.Request) -> web.Response:
    session: aiohttp.ClientSession = request.app["session"]
    upstream: str = request.app["upstream"]
    console = logging.getLogger("console")

    seq = next(_sequence)
    timestamp = datetime.now().isoformat(sep=" ", timespec="milliseconds")

    # --- Read request body ---
    req_body = await request.read()

    # --- Build forwarded headers ---
    # Also strip headers named in the Connection header value
    conn_extras = {
        h.strip().lower()
        for h in request.headers.get("Connection", "").split(",")
        if h.strip()
    }
    req_headers = _filter_headers(request.headers, extra_skip=conn_extras)
    # Set Host to the upstream host
    upstream_host = URL(upstream).authority
    req_headers["Host"] = upstream_host

    target_url = upstream.rstrip("/") + str(request.rel_url)

    # --- Console: log request ---
    peername = request.transport.get_extra_info("peername") if request.transport else None
    client_addr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
    sep = "─" * 60
    req_block = "\n".join([
        "",
        f"{_CYAN}{sep}{_RESET}",
        f"{_BOLD + _CYAN}REQUEST  #{seq}  {timestamp}  {client_addr}{_RESET}",
        f"{_CYAN}{sep}{_RESET}",
        f"{request.method} {target_url}",
        *[f"{k}: {v}" for k, v in req_headers.items()],
        "",
        _body_preview(req_body) if req_body else "(no body)",
    ])
    console.info(req_block)

    # --- Forward to upstream ---
    start = time.monotonic()
    try:
        async with session.request(
            method=request.method,
            url=target_url,
            headers=req_headers,
            data=req_body,
            allow_redirects=False,
        ) as upstream_resp:
            resp_body = await upstream_resp.read()
            elapsed_ms = (time.monotonic() - start) * 1000

            resp_headers = _filter_headers(dict(upstream_resp.headers))

            # --- Console: log response ---
            sc = _status_color(upstream_resp.status)
            resp_block = "\n".join([
                "",
                f"{sc}{sep}{_RESET}",
                f"{_BOLD + sc}RESPONSE  #{seq}  +{elapsed_ms:.1f}ms{_RESET}",
                f"{sc}{sep}{_RESET}",
                f"{upstream_resp.status} {upstream_resp.reason}",
                *[f"{k}: {v}" for k, v in resp_headers.items()],
                "",
                _body_preview(resp_body) if resp_body else "(no body)",
                "",
            ])
            console.info(resp_block)

            # --- File: log full ---
            _log_to_file(
                seq=seq,
                timestamp=timestamp,
                client_addr=client_addr,
                req_method=request.method,
                req_url=target_url,
                req_headers=req_headers,
                req_body=req_body,
                resp_status=upstream_resp.status,
                resp_reason=upstream_resp.reason or "",
                resp_headers=resp_headers,
                resp_body=resp_body,
                elapsed_ms=elapsed_ms,
            )

            return web.Response(
                status=upstream_resp.status,
                reason=upstream_resp.reason,
                headers=resp_headers,
                body=resp_body,
            )

    except aiohttp.ClientError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        console.error("%s[#%d] !!! upstream error: %s  (%.1fms)%s", _BOLD + _RED, seq, exc, elapsed_ms, _RESET)
        return web.Response(status=502, text=f"Bad Gateway: {exc}")


async def on_startup(app: web.Application) -> None:
    app["session"] = aiohttp.ClientSession()


async def on_cleanup(app: web.Application) -> None:
    await app["session"].close()


def _setup_logging(log_file: str) -> None:
    global _file_logger

    # Console logger
    console = logging.getLogger("console")
    console.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    console.addHandler(ch)

    # File logger (full, raw blocks — no extra formatting)
    _file_logger = logging.getLogger("file")
    _file_logger.setLevel(logging.DEBUG)
    _file_logger.propagate = False
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    _file_logger.addHandler(fh)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="simple-reverse-proxy",
        description="Simple HTTP reverse proxy server",
    )
    parser.add_argument(
        "upstream",
        help="Upstream base URL to proxy to (e.g. http://localhost:8080)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Listen host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9091,
        help="Listen port (default: 9091)",
    )
    parser.add_argument(
        "--log-file",
        default="reverse-proxy.log",
        dest="log_file",
        help="Path to the full request/response log file (default: proxy.log)",
    )
    args = parser.parse_args()

    _setup_logging(args.log_file)

    console = logging.getLogger("console")
    console.info(
        "Starting reverse proxy  %s:%d  -->  %s",
        args.host,
        args.port,
        args.upstream,
    )
    console.info("Logging traffic to: %s", args.log_file)

    app = web.Application()
    app["upstream"] = args.upstream.rstrip("/")
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("*", "/{path_info:.*}", handle)

    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()

"""DatabaseMax PostgreSQL gateway.

Flow per client connection:
  1. Read the first packet. GSSENCRequest -> 'N'. SSLRequest -> 'S' + TLS handshake (SNI captured).
     Plain StartupMessage -> ErrorResponse "SSL required". CancelRequest -> routed via key map.
  2. Read the StartupMessage over TLS, extract user/database.
  3. Resolve the route: SNI route id (if the SNI host is under DMX_DB_HOST_SUFFIX) and the
     username must identify the same database. Reserved users are refused.
  4. Enforce per-database concurrent connection cap and per-IP connection rate.
  5. Connect to the instance over the internal network, forward the StartupMessage verbatim and
     relay traffic. Authentication (SCRAM) happens end-to-end between client and PostgreSQL.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import signal
import socket
import ssl
import struct
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass

import httpx

from dmx_gateway.protocol import (
    CANCEL_REQUEST,
    GSSENC_REQUEST,
    MAX_STARTUP_LEN,
    PROTOCOL_3_0,
    SSL_REQUEST,
    ProtocolError,
    error_response,
    parse_startup_params,
)

logging.basicConfig(
    stream=sys.stdout,
    level=os.environ.get("DMX_LOG_LEVEL", "INFO"),
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"dmx.gateway","msg":"%(message)s"}',
)
log = logging.getLogger("dmx.gateway")

RESERVED_USERS = {"dmx_admin", "postgres"}
GENERIC_ROUTE_ERROR = "unknown endpoint or user; check the host and username in your connection URL"


@dataclass
class Config:
    listen_host: str = os.environ.get("DMX_GATEWAY_HOST", "0.0.0.0")  # noqa: S104  # nosec B104 - public listener by design
    listen_port: int = int(os.environ.get("DMX_GATEWAY_PORT", "5432"))
    status_host: str = os.environ.get("DMX_GATEWAY_STATUS_HOST", "127.0.0.1")
    status_port: int = int(os.environ.get("DMX_GATEWAY_STATUS_PORT", "9187"))
    cert_file: str = os.environ.get("DMX_GATEWAY_CERT", "certs/gateway.crt")
    key_file: str = os.environ.get("DMX_GATEWAY_KEY", "certs/gateway.key")
    internal_api: str = os.environ.get("DMX_INTERNAL_API_URL", "http://127.0.0.1:8001")
    internal_token: str = os.environ.get("INTERNAL_API_TOKEN", "dev-only-internal-token")
    host_suffix: str | None = os.environ.get("DMX_DB_HOST_SUFFIX") or None
    handshake_timeout: float = float(os.environ.get("DMX_GATEWAY_HANDSHAKE_TIMEOUT", "10"))
    backend_connect_timeout: float = 5.0
    route_cache_ttl: float = float(os.environ.get("DMX_GATEWAY_ROUTE_TTL", "10"))
    per_ip_rate: int = int(os.environ.get("DMX_GATEWAY_PER_IP_RATE", "60"))  # new connections / window
    per_ip_window: float = 10.0
    max_total_connections: int = int(os.environ.get("DMX_GATEWAY_MAX_CONNECTIONS", "5000"))
    # Concurrent connections from one client IP across all databases (a single noisy client
    # can't take the whole gateway), and TCP keepalive so dead peers release their slots.
    per_ip_max_active: int = int(os.environ.get("DMX_GATEWAY_PER_IP_MAX_ACTIVE", "300"))
    keepalive_idle: int = int(os.environ.get("DMX_GATEWAY_KEEPALIVE_IDLE", "60"))
    # Largest single server message relayed to the dashboard's SQL editor (one huge row must not
    # exhaust the API's memory). Other clients are not affected.
    editor_max_message: int = int(os.environ.get("DMX_GATEWAY_EDITOR_MAX_MESSAGE", str(8 * 1024 * 1024)))


# application_name the API's SQL editor connects with (app/sqlconsole/engine.py). A customer who
# sets it on their own client only limits their own connection.
EDITOR_APPLICATION_NAME = "dmx-sql-editor"


class MessageSizeGuard:
    """Follows PostgreSQL message framing on the server→client stream and reports a message
    larger than `limit` bytes before any of its body is relayed."""

    def __init__(self, limit: int):
        self.limit = limit
        self.header = b""
        self.skip = 0

    def feed(self, data: bytes) -> bool:
        i, n = 0, len(data)
        while i < n:
            if self.skip:
                take = min(self.skip, n - i)
                self.skip -= take
                i += take
                continue
            need = 5 - len(self.header)
            self.header += data[i : i + need]
            i += min(need, n - i)
            if len(self.header) < 5:
                break
            (length,) = struct.unpack("!I", self.header[1:5])
            self.header = b""
            if length < 4 or length > self.limit:
                return False
            self.skip = length - 4
        return True


@dataclass
class Route:
    route_id: str
    username: str
    backend_host: str
    backend_port: int
    max_connections: int
    status: str


class RouteError(Exception):
    def __init__(self, message: str, code: str = "08004"):
        super().__init__(message)
        self.code = code


class Gateway:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ssl_ctx = self._make_ssl()
        self.active: dict[str, int] = defaultdict(int)
        self.total_active = 0
        self.ip_active: dict[str, int] = defaultdict(int)
        self.cancel_map: dict[bytes, tuple[str, int]] = {}
        self.ip_hits: dict[str, deque[float]] = defaultdict(deque)
        self.route_cache: dict[tuple[str | None, str], tuple[float, Route | None]] = {}
        self.http = httpx.AsyncClient(
            base_url=cfg.internal_api,
            timeout=5.0,
            headers={"Authorization": f"Bearer {cfg.internal_token}"},
        )
        self.stats = {"accepted": 0, "rejected": 0, "tls_required": 0, "routed": 0, "cancel": 0, "oversized": 0}
        # Per database: [bytes client→server, bytes server→client, connections routed] (cumulative).
        self.traffic: dict[str, list[int]] = {}
        self.started = time.time()

    # --- TLS -------------------------------------------------------------------------------
    def _make_ssl(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.options |= ssl.OP_NO_COMPRESSION | ssl.OP_CIPHER_SERVER_PREFERENCE
        ctx.load_cert_chain(self.cfg.cert_file, self.cfg.key_file)
        ctx.set_alpn_protocols(["postgresql"])

        def sni_cb(sslobj: ssl.SSLObject, server_name: str | None, _ctx: ssl.SSLContext) -> None:
            sslobj.dmx_sni = (server_name or "").lower()  # type: ignore[attr-defined]

        ctx.sni_callback = sni_cb  # type: ignore[assignment]
        return ctx

    def reload_tls(self) -> None:
        try:
            self.ssl_ctx = self._make_ssl()
            log.info("TLS certificate reloaded")
        except Exception as e:
            log.error("TLS reload failed: %s", type(e).__name__)

    # --- routing ----------------------------------------------------------------------------
    def route_id_from_sni(self, sni: str | None) -> str | None:
        if not sni or not self.cfg.host_suffix:
            return None
        suffix = "." + self.cfg.host_suffix.lower().lstrip(".")
        if sni.endswith(suffix):
            label = sni[: -len(suffix)]
            if label and "." not in label:
                return label
        return None

    async def resolve(self, route_id: str | None, username: str) -> Route:
        if not username or username in RESERVED_USERS or username.startswith(("pg_", "dmx_")):
            raise RouteError(GENERIC_ROUTE_ERROR, "28000")
        key = (route_id, username)
        now = time.monotonic()
        cached = self.route_cache.get(key)
        if cached and cached[0] > now:
            route = cached[1]
        else:
            params = {"username": username}
            if route_id:
                params["route_id"] = route_id
            try:
                r = await self.http.get("/internal/v1/gateway/route", params=params)
            except httpx.HTTPError as e:
                raise RouteError("routing service unavailable, try again", "57P03") from e
            if r.status_code == 404:
                route = None
            elif r.status_code != 200:
                raise RouteError("routing service unavailable, try again", "57P03")
            else:
                d = r.json()
                route = Route(
                    d["route_id"],
                    d["username"],
                    d["backend_host"],
                    int(d["backend_port"]),
                    int(d["max_connections"]),
                    d["status"],
                )
            ttl = self.cfg.route_cache_ttl if route else 2.0
            self.route_cache[key] = (now + ttl, route)
            if len(self.route_cache) > 50_000:
                self.route_cache.clear()
        if route is None:
            raise RouteError(GENERIC_ROUTE_ERROR, "28000")
        if route_id and route.route_id != route_id:
            raise RouteError(GENERIC_ROUTE_ERROR, "28000")
        if route.status == "stopped":
            raise RouteError("database is stopped; start it from the DatabaseMax dashboard", "57P01")
        if route.status == "migrating":
            raise RouteError("database is being moved to another server; try again in a few minutes", "57P03")
        if route.status == "host_unavailable":
            raise RouteError("the server hosting this database is unavailable; operators have been alerted", "57P03")
        if route.status not in ("ready", "testing"):
            raise RouteError("database is not available", "57P03")
        return route

    def keepalive(self, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        with contextlib.suppress(OSError, AttributeError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, self.cfg.keepalive_idle)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 15)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 4)

    def ip_slot_ok(self, ip: str) -> bool:
        return self.ip_active[ip] < self.cfg.per_ip_max_active

    @staticmethod
    def client_key(ip: str) -> str:
        """Limits apply per IPv4 address and per IPv6 /64 (one subscriber usually owns a whole
        /64, so per-address limits would be trivial to rotate around)."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if isinstance(addr, ipaddress.IPv6Address):
            if addr.ipv4_mapped:
                return str(addr.ipv4_mapped)
            return str(ipaddress.IPv6Network(f"{addr}/64", strict=False))
        return ip

    def rate_ok(self, ip: str) -> bool:
        now = time.monotonic()
        q = self.ip_hits[ip]
        while q and q[0] < now - self.cfg.per_ip_window:
            q.popleft()
        if len(q) >= self.cfg.per_ip_rate:
            return False
        q.append(now)
        if len(self.ip_hits) > 100_000:
            # Evict idle clients only — never reset the windows of clients that are active.
            cutoff = now - self.cfg.per_ip_window
            for k in [k for k, v in self.ip_hits.items() if not v or v[-1] < cutoff]:
                del self.ip_hits[k]
        return True

    # --- connection handling ------------------------------------------------------------------
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = self.client_key(peer[0]) if peer else "unknown"
        self.stats["accepted"] += 1
        if not self.rate_ok(ip) or not self.ip_slot_ok(ip):
            self.stats["rejected"] += 1
            writer.close()
            return
        self.ip_active[ip] += 1
        self.keepalive(writer)
        route: Route | None = None
        counted = False
        backend_writer: asyncio.StreamWriter | None = None
        try:
            try:
                startup = await asyncio.wait_for(self._negotiate(reader, writer), self.cfg.handshake_timeout)
            except (TimeoutError, ProtocolError, ConnectionError, ssl.SSLError):
                self.stats["rejected"] += 1
                return
            if startup is None:
                return
            protocol, body, sni = startup
            params = parse_startup_params(body)
            username = params.get("user", "")
            try:
                route = await self.resolve(self.route_id_from_sni(sni), username)
                if self.active[route.route_id] >= route.max_connections:
                    raise RouteError(f"too many connections for this database (limit {route.max_connections})", "53300")
                if self.total_active >= self.cfg.max_total_connections:
                    raise RouteError("gateway at capacity, try again shortly", "53300")
            except RouteError as e:
                self.stats["rejected"] += 1
                writer.write(error_response(str(e), e.code))
                await writer.drain()
                return
            self.active[route.route_id] += 1
            self.total_active += 1
            counted = True
            try:
                breader, bwriter = await asyncio.wait_for(
                    asyncio.open_connection(route.backend_host, route.backend_port),
                    self.cfg.backend_connect_timeout,
                )
                backend_writer = bwriter
                self.keepalive(bwriter)
            except (TimeoutError, OSError):
                writer.write(error_response("database is not reachable right now", "08006"))
                await writer.drain()
                return
            bwriter.write(struct.pack("!II", len(body) + 8, protocol) + body)
            await bwriter.drain()
            self.stats["routed"] += 1
            traffic = self.traffic.setdefault(route.route_id, [0, 0, 0])
            traffic[2] += 1
            guard = (
                MessageSizeGuard(self.cfg.editor_max_message)
                if params.get("application_name") == EDITOR_APPLICATION_NAME
                else None
            )
            keys: list[bytes] = []
            try:
                await self._pipe_both(reader, writer, breader, bwriter, route, keys, traffic, guard)
            finally:
                for k in keys:
                    self.cancel_map.pop(k, None)
        except Exception as e:
            log.warning("connection error: %s", type(e).__name__)
        finally:
            self.ip_active[ip] -= 1
            if self.ip_active[ip] <= 0:
                self.ip_active.pop(ip, None)
            if counted and route is not None:
                self.active[route.route_id] -= 1
                if self.active[route.route_id] <= 0:
                    self.active.pop(route.route_id, None)
                self.total_active -= 1
            for w in (backend_writer, writer):
                if w is not None:
                    with contextlib.suppress(Exception):
                        w.close()

    async def _read_packet(self, reader: asyncio.StreamReader) -> tuple[int, bytes]:
        header = await reader.readexactly(8)
        length, code = struct.unpack("!II", header)
        if length < 8 or length > MAX_STARTUP_LEN:
            raise ProtocolError("bad startup length")
        rest = await reader.readexactly(length - 8)
        return code, rest

    async def _negotiate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> tuple[int, bytes, str | None] | None:
        tls = False
        sni: str | None = None
        for _ in range(3):
            code, rest = await self._read_packet(reader)
            if code == GSSENC_REQUEST and not tls:
                writer.write(b"N")
                await writer.drain()
                continue
            if code == SSL_REQUEST and not tls:
                writer.write(b"S")
                await writer.drain()
                await writer.start_tls(self.ssl_ctx)
                sslobj = writer.get_extra_info("ssl_object")
                sni = getattr(sslobj, "dmx_sni", None) or None
                tls = True
                continue
            if code == CANCEL_REQUEST:
                await self._cancel(rest)
                return None
            if code == PROTOCOL_3_0:
                if not tls:
                    self.stats["tls_required"] += 1
                    writer.write(
                        error_response("SSL/TLS is required. Add sslmode=require to your connection string.", "28000")
                    )
                    await writer.drain()
                    return None
                return code, rest, sni
            writer.write(error_response("unsupported protocol version", "0A000"))
            await writer.drain()
            return None
        raise ProtocolError("too many negotiation packets")

    async def _cancel(self, rest: bytes) -> None:
        self.stats["cancel"] += 1
        target = self.cancel_map.get(rest[:8])
        if not target:
            return
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(*target), 3)
            w.write(struct.pack("!II", 16, CANCEL_REQUEST) + rest[:8])
            await w.drain()
            w.close()
        except (TimeoutError, OSError):
            pass

    async def _pipe_both(
        self,
        creader: asyncio.StreamReader,
        cwriter: asyncio.StreamWriter,
        breader: asyncio.StreamReader,
        bwriter: asyncio.StreamWriter,
        route: Route,
        keys: list[bytes],
        traffic: list[int] | None = None,
        guard: MessageSizeGuard | None = None,
    ) -> None:
        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, watch_keys: bool) -> None:
            buf = b""
            parsing = watch_keys
            direction = 1 if watch_keys else 0  # watch_keys is set on the server→client pump
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    if traffic is not None:
                        traffic[direction] += len(data)
                    if watch_keys and guard is not None and not guard.feed(data):
                        self.stats["oversized"] += 1
                        break
                    if parsing:
                        buf += data
                        while len(buf) >= 5:
                            (length,) = struct.unpack("!I", buf[1:5])
                            if len(buf) < 1 + length:
                                break
                            mtype, payload = buf[:1], buf[5 : 1 + length]
                            if mtype == b"K" and len(payload) >= 8:
                                self.cancel_map[payload[:8]] = (route.backend_host, route.backend_port)
                                keys.append(payload[:8])
                            if mtype == b"Z":
                                parsing = False
                                buf = b""
                                break
                            buf = buf[1 + length :]
                        if len(buf) > 1_000_000:
                            parsing = False
                            buf = b""
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    if dst.can_write_eof():
                        dst.write_eof()

        t1 = asyncio.create_task(pump(creader, bwriter, False))
        t2 = asyncio.create_task(pump(breader, cwriter, True))
        _done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    # --- status endpoint (private) ----------------------------------------------------------
    async def status(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            body = json.dumps(
                {
                    "status": "ok",
                    "uptime_seconds": int(time.time() - self.started),
                    "active_total": self.total_active,
                    "active_by_route": dict(self.active),
                    "traffic_by_route": {
                        rid: {"bytes_in": t[0], "bytes_out": t[1], "connections_total": t[2]}
                        for rid, t in self.traffic.items()
                    },
                    "counters": self.stats,
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except Exception:  # noqa: S110
            pass
        finally:
            writer.close()


async def main() -> None:
    cfg = Config()
    gw = Gateway(cfg)
    server = await asyncio.start_server(gw.handle, cfg.listen_host, cfg.listen_port, reuse_address=True, backlog=1024)
    status = await asyncio.start_server(gw.status, cfg.status_host, cfg.status_port)
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGHUP, gw.reload_tls)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    log.info(
        "gateway listening on %s:%s (status %s:%s)",
        cfg.listen_host,
        cfg.listen_port,
        cfg.status_host,
        cfg.status_port,
    )
    async with server, status:
        await stop.wait()
    await gw.http.aclose()


if __name__ == "__main__":
    asyncio.run(main())

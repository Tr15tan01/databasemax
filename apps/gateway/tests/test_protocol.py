import struct

import pytest

from dmx_gateway.protocol import ProtocolError, error_response, parse_startup_params
from dmx_gateway.server import Config, Gateway, RouteError


def test_parse_startup_params():
    body = b"user\x00u_abc\x00database\x00app\x00application_name\x00psql\x00\x00"
    assert parse_startup_params(body) == {"user": "u_abc", "database": "app", "application_name": "psql"}


def test_parse_malformed():
    with pytest.raises(ProtocolError):
        parse_startup_params(b"user")


def test_error_response_format():
    msg = error_response("nope", "28000")
    assert msg[:1] == b"E"
    (length,) = struct.unpack("!I", msg[1:5])
    assert length == len(msg) - 1
    assert b"C28000\x00" in msg and b"Mnope\x00" in msg


class _GW(Gateway):
    def _make_ssl(self):  # no certificate needed for routing unit tests
        return None


def _gw(suffix="db.example.com"):
    cfg = Config()
    cfg.host_suffix = suffix
    return _GW(cfg)


def test_route_id_from_sni():
    gw = _gw()
    assert gw.route_id_from_sni("db-abc123.db.example.com") == "db-abc123"
    assert gw.route_id_from_sni("evil.db-abc123.db.example.com") is None
    assert gw.route_id_from_sni("db.example.com") is None
    assert gw.route_id_from_sni("db-abc123.other.com") is None
    assert gw.route_id_from_sni(None) is None


@pytest.mark.parametrize("user", ["dmx_admin", "postgres", "pg_monitor", "dmx_anything", ""])
async def _reserved(user):
    gw = _gw()
    with pytest.raises(RouteError):
        await gw.resolve(None, user)


@pytest.mark.parametrize("user", ["dmx_admin", "postgres", "pg_monitor", "dmx_anything", ""])
def test_reserved_users_rejected(user):
    import asyncio

    asyncio.run(_reserved(user))


def test_rate_limit_per_ip():
    gw = _gw()
    gw.cfg.per_ip_rate = 3
    assert [gw.rate_ok("1.2.3.4") for _ in range(4)] == [True, True, True, False]
    assert gw.rate_ok("5.6.7.8")


def test_per_ip_concurrency_cap():
    gw = _gw()
    gw.cfg.per_ip_max_active = 2
    gw.ip_active["1.2.3.4"] = 2
    assert not gw.ip_slot_ok("1.2.3.4")
    assert gw.ip_slot_ok("5.6.7.8")


def test_client_key_groups_ipv6_by_64():
    gw = _gw()
    assert gw.client_key("2001:db8:1:2:aaaa::1") == gw.client_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert gw.client_key("2001:db8:1:3::1") != gw.client_key("2001:db8:1:2::1")
    assert gw.client_key("::ffff:10.0.0.1") == "10.0.0.1"
    assert gw.client_key("192.0.2.7") == "192.0.2.7"


def test_rate_table_eviction_keeps_active_clients():
    import time as t

    gw = _gw()
    gw.cfg.per_ip_rate = 2
    assert gw.rate_ok("active") and gw.rate_ok("active") and not gw.rate_ok("active")
    for i in range(100_001):
        gw.ip_hits[f"idle{i}"].append(t.monotonic() - 3600)
    gw.rate_ok("new")
    assert "idle5" not in gw.ip_hits
    assert not gw.rate_ok("active")  # its window survived the eviction


def _msg(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!I", len(body) + 4) + body


def test_message_size_guard_allows_small_messages_across_chunk_boundaries():
    from dmx_gateway.server import MessageSizeGuard

    stream = _msg(b"T", b"x" * 10) + _msg(b"D", b"y" * 1000) + _msg(b"Z", b"I")
    g = MessageSizeGuard(limit=2000)
    # Feed one byte at a time: headers and bodies split anywhere must still be framed correctly.
    assert all(g.feed(stream[i : i + 1]) for i in range(len(stream)))
    g2 = MessageSizeGuard(limit=2000)
    assert g2.feed(stream)


def test_message_size_guard_rejects_oversized_message_from_its_header():
    from dmx_gateway.server import MessageSizeGuard

    g = MessageSizeGuard(limit=100)
    assert g.feed(_msg(b"D", b"a" * 50))
    # Only the header of the large row has arrived: it is refused before any body is relayed.
    big_header = b"D" + struct.pack("!I", 10_000_000)
    assert g.feed(big_header[:3])
    assert not g.feed(big_header[3:])


def test_message_size_guard_rejects_malformed_length():
    from dmx_gateway.server import MessageSizeGuard

    assert not MessageSizeGuard(limit=100).feed(b"D" + struct.pack("!I", 2))

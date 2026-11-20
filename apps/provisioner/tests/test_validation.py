import pytest

from dmx_provisioner import validation as v
from dmx_provisioner.scram import admin_password, verifier


@pytest.mark.parametrize("bad", ["", "ABC123456789", "short", "a" * 33, "abc/../../etc", "abc 123456789", "x;rm -rf"])
def test_instance_id_rejects(bad):
    with pytest.raises(v.ValidationFailed):
        v.instance_id(bad)


def test_instance_id_accepts():
    assert v.instance_id("abcdef123456") == "abcdef123456"


@pytest.mark.parametrize("bad", ["postgres", "template1", "Upper", "1db", 'x"; drop', "a-b", ""])
def test_db_name_rejects(bad):
    with pytest.raises(v.ValidationFailed):
        v.db_name(bad)


@pytest.mark.parametrize("bad", ["dmx_admin", "postgres", "pg_read_all_data", "dmx_x", "public", "U1"])
def test_role_rejects(bad):
    with pytest.raises(v.ValidationFailed):
        v.role_name(bad)


def test_scram_validation():
    assert v.scram(verifier("secret-password"))
    for bad in ("plaintext", "SCRAM-SHA-256$4096:abc$def:ghi", "SCRAM-SHA-256$4096:x'; drop role x; --$a:b"):
        with pytest.raises(v.ValidationFailed):
            v.scram(bad)


def test_admin_password_is_per_instance_and_deterministic():
    a = admin_password("s", "instance000001")
    assert a == admin_password("s", "instance000001")
    assert a != admin_password("s", "instance000002")
    assert a != admin_password("t", "instance000001")


def test_api_requires_token():
    from fastapi.testclient import TestClient

    from dmx_provisioner.main import app

    c = TestClient(app)
    assert c.get("/v1/instances").status_code == 401
    assert c.get("/v1/instances", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # Unknown fields (e.g. an image) are rejected even with a valid token.
    r = c.put(
        "/v1/instances/abcdef123456",
        headers={"Authorization": "Bearer dev-only-provisioner-token"},
        json={
            "pg_version": "16",
            "profile": "nano",
            "storage_mb": 500,
            "max_connections": 20,
            "database_id": "d",
            "owner_id": "o",
            "image": "evil/image",
        },
    )
    assert r.status_code == 422

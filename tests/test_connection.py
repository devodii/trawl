import sys

from trawl.schema.connection import Connection

NEON = "postgresql://owner:p%40ss@ep-cool-1234.us-east-2.aws.neon.tech/hackproof?sslmode=require&channel_binding=require"


def test_a_connection_is_read_from_a_url():
    connection = Connection.from_url(NEON)

    assert connection.host == "ep-cool-1234.us-east-2.aws.neon.tech"
    assert connection.port == 5432
    assert connection.user == "owner"
    assert connection.password == "p@ss"
    assert connection.database == "hackproof"
    assert connection.sslmode == "require"
    assert not connection.is_local


def test_every_part_of_a_remote_url_is_masked_in_ci_logs():
    lines = Connection.from_url(NEON).mask_lines()

    assert "::add-mask::ep-cool-1234.us-east-2.aws.neon.tech" in lines
    assert "::add-mask::owner" in lines
    assert "::add-mask::p@ss" in lines
    assert "::add-mask::p%40ss" in lines


def test_a_local_database_is_reached_through_the_host_the_container_can_see(
    monkeypatch,
):
    local = Connection.from_url("postgresql://u:p@localhost:5440/hackproof")

    monkeypatch.setattr(sys, "platform", "darwin")
    assert local.container_route() == (
        ["--add-host", "host.docker.internal:host-gateway"],
        "host.docker.internal",
    )
    monkeypatch.setattr(sys, "platform", "linux")
    assert local.container_route() == (["--network", "host"], "localhost")
    assert Connection.from_url(NEON).container_route() == (
        [],
        "ep-cool-1234.us-east-2.aws.neon.tech",
    )

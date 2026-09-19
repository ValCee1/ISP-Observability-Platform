import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return FIXTURES


class FakeClient:
    """Stands in for client.APIClient. Maps API path -> fixture rows."""

    def __init__(self, mapping: dict[str, list]):
        self._mapping = mapping
        self.closed = False

    def query(self, path: str, **where):
        return list(self._mapping.get(path.rstrip("/"), []))

    def command(self, path: str, **params):
        return list(self._mapping.get(path.rstrip("/"), []))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient(
        {
            "/ppp/active/print": load("ppp_active.json"),
            "/ppp/secret/print": load("ppp_secret.json"),
            "/log/print": load("log_pppoe.json"),
            "/system/resource/print": load("system_resource.json"),
            "/ip/route/print": load("ip_route.json"),
            "/ip/neighbor/print": load("ip_neighbor.json"),
            "/interface/print": load("interfaces.json"),
        }
    )

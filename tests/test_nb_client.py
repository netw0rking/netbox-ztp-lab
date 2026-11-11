from unittest.mock import MagicMock
from netbox.nb_client import get_or_create


def test_get_or_create_returns_existing_without_creating():
    endpoint = MagicMock()
    existing = MagicMock()
    endpoint.get.return_value = existing

    result = get_or_create(endpoint, {"slug": "spine"})

    assert result is existing
    endpoint.get.assert_called_once_with(slug="spine")
    endpoint.create.assert_not_called()


def test_get_or_create_creates_when_missing():
    endpoint = MagicMock()
    endpoint.get.return_value = None
    created = MagicMock()
    endpoint.create.return_value = created

    result = get_or_create(endpoint, {"slug": "spine"}, defaults={"name": "Spine"})

    assert result is created
    endpoint.create.assert_called_once_with(slug="spine", name="Spine")


def test_get_client_sends_full_bearer_header_not_classic_token_scheme(monkeypatch):
    monkeypatch.setenv("nb_url", "http://netbox.example.com")
    monkeypatch.setenv("nb_token", "Bearer abc123.def456")

    from netbox.nb_client import get_client

    nb = get_client()

    assert nb.http_session.headers["Authorization"] == "Bearer abc123.def456"

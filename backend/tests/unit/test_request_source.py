from app.api.middleware import resolve_request_source
from app.core.config import Settings


def _settings() -> Settings:
    return Settings(
        trusted_gateway_networks=("10.0.0.0/8",),
        gateway_shared_secret="gateway-secret",
    )


def test_trusted_gateway_can_supply_forwarded_for() -> None:
    assert (
        resolve_request_source(
            client_host="10.1.2.3",
            forwarded_for="203.0.113.10, 10.1.2.3",
            gateway_secret="gateway-secret",
            settings=_settings(),
        )
        == "203.0.113.10"
    )


def test_untrusted_forwarded_for_uses_direct_client_address() -> None:
    settings = _settings()
    assert (
        resolve_request_source(
            client_host="198.51.100.6",
            forwarded_for="203.0.113.10",
            gateway_secret="gateway-secret",
            settings=settings,
        )
        == "198.51.100.6"
    )
    assert (
        resolve_request_source(
            client_host="10.1.2.3",
            forwarded_for="203.0.113.10",
            gateway_secret="wrong",
            settings=settings,
        )
        == "10.1.2.3"
    )

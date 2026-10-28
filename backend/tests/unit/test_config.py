import os

from app.config import AppSettings


def test_settings_load_token_and_redact_summary(monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "secret-token")
    settings = AppSettings(
        database_url="postgresql+asyncpg://u:p@db/app",
        ha_url="http://homeassistant:8123",
        ha_token=os.environ["HA_TOKEN"],
    )

    assert settings.ha_token == "secret-token"
    assert settings.safe_summary()["ha_token"] == "[redacted]"
    assert "secret-token" not in repr(settings.safe_summary())


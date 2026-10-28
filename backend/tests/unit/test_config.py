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


def test_settings_include_media_archive_and_api_controls():
    settings = AppSettings(
        database_url="postgresql+asyncpg://u:p@db/app",
        api_token="api-secret",
        frigate_url="http://frigate:5000",
        media_archive_root="/media_archive",
        media_archive_max_bytes=512,
        media_archive_reserve_free_bytes=1024,
    )

    assert settings.api_token == "api-secret"
    assert settings.frigate_url == "http://frigate:5000"
    assert settings.media_archive_root == "/media_archive"
    assert settings.media_archive_max_bytes == 512
    assert settings.media_archive_reserve_free_bytes == 1024

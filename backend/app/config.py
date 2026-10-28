from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging import redact_mapping


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(validation_alias="DATABASE_URL")
    postgres_data_path: str = Field(default="/var/lib/postgresql/data", validation_alias="POSTGRES_DATA_PATH")
    database_max_bytes: int = Field(default=2 * 1024**3, validation_alias="DATABASE_MAX_BYTES")

    mqtt_host: str = Field(default="mqtt", validation_alias="MQTT_HOST")
    mqtt_port: int = Field(default=1883, validation_alias="MQTT_PORT")
    mqtt_client_id: str = Field(default="home-server-ingestor", validation_alias="MQTT_CLIENT_ID")
    mqtt_username: str | None = Field(default=None, validation_alias="MQTT_USERNAME")
    mqtt_password: str | None = Field(default=None, validation_alias="MQTT_PASSWORD")
    mqtt_topics: tuple[str, ...] = Field(
        default=("frigate/events", "frigate/reviews", "frigate/notifications", "frigate/available"),
        validation_alias="MQTT_TOPICS",
    )
    mqtt_max_inflight: int = Field(default=100, validation_alias="MQTT_MAX_INFLIGHT")

    ha_url: str = Field(default="http://homeassistant:8123", validation_alias="HA_URL")
    ha_token: str = Field(default="", validation_alias="HA_TOKEN")
    ha_event_types: tuple[str, ...] = Field(
        default=("state_changed", "automation_triggered", "call_service"),
        validation_alias="HA_EVENT_TYPES",
    )
    ha_history_max_lookback_seconds: int = Field(default=900, validation_alias="HA_HISTORY_MAX_LOOKBACK_SECONDS")
    ha_history_overlap_seconds: int = Field(default=5, validation_alias="HA_HISTORY_OVERLAP_SECONDS")

    api_host: str = Field(default="127.0.0.1", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    retry_max_attempts: int = Field(default=8, validation_alias="RETRY_MAX_ATTEMPTS")
    retry_base_seconds: float = Field(default=0.5, validation_alias="RETRY_BASE_SECONDS")
    retry_max_delay_seconds: float = Field(default=30.0, validation_alias="RETRY_MAX_DELAY_SECONDS")

    disk_warning_free_bytes: int = Field(default=4 * 1024**3, validation_alias="DISK_WARNING_FREE_BYTES")
    disk_critical_free_bytes: int = Field(default=2 * 1024**3, validation_alias="DISK_CRITICAL_FREE_BYTES")
    disk_warning_free_ratio: float = Field(default=0.20, validation_alias="DISK_WARNING_FREE_RATIO")
    disk_critical_free_ratio: float = Field(default=0.10, validation_alias="DISK_CRITICAL_FREE_RATIO")

    @field_validator("ha_event_types", mode="before")
    @classmethod
    def parse_event_types(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return tuple(value)

    @field_validator("mqtt_topics", mode="before")
    @classmethod
    def parse_mqtt_topics(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return tuple(value)

    def safe_summary(self) -> dict[str, Any]:
        return redact_mapping(self.model_dump())


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()

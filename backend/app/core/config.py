from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    public_url: str
    database_url: str
    redis_url: str
    secret_key: str
    master_encryption_key: str
    files_backend: Literal["local", "s3"] = "local"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-2"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    tz: str = "Asia/Seoul"

    @field_validator("public_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith("https://") or "/" in value[8:].rstrip("/"):
            raise ValueError("PUBLIC_URL must be an HTTPS origin")
        return value.rstrip("/")

    @field_validator("secret_key", "master_encryption_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if len(bytes.fromhex(value)) != 32:
            raise ValueError("Key must contain 32 random bytes as hex")
        return value


@lru_cache
def config() -> Config:
    return Config()

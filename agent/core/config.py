"""全局配置管理"""

import logging
import secrets

from pydantic_settings import BaseSettings
from pydantic import Field, model_validator
from functools import lru_cache

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """应用全局配置，从环境变量加载"""

    # LLM 配置 - 阿里云通义千问
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="DASHSCOPE_BASE_URL",
    )
    model_qwen_max: str = Field(default="qwen-max", alias="MODEL_QWEN_MAX")
    model_qwen_plus: str = Field(default="qwen-plus", alias="MODEL_QWEN_PLUS")
    model_qwen_turbo: str = Field(default="qwen-turbo", alias="MODEL_QWEN_TURBO")

    # PostgreSQL
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="agent_platform", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")

    # Redis
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")
    redis_db: int = Field(default=0, alias="REDIS_DB")

    # Langfuse
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", alias="LANGFUSE_HOST"
    )

    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_service_name: str = Field(
        default="multi-agent-office-platform", alias="OTEL_SERVICE_NAME"
    )

    # API 服务
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_workers: int = Field(default=4, alias="API_WORKERS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # API 版本
    api_version: str = Field(default="v1", alias="API_VERSION")

    # CORS 允许的来源（逗号分隔）
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:8080",
        alias="CORS_ALLOWED_ORIGINS",
    )

    # 安全
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")

    # 限流
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    rate_limit_burst: int = Field(default=100, alias="RATE_LIMIT_BURST")

    # MCP Registry
    mcp_registry_url: str = Field(
        default="http://localhost:9099", alias="MCP_REGISTRY_URL"
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_security_config(self) -> "Settings":
        """安全配置校验：非开发环境必须设置 JWT 密钥"""
        if self.environment != "development" and not self.jwt_secret_key:
            raise ValueError(
                "生产环境必须设置 JWT_SECRET_KEY 环境变量，"
                "可通过 `python -c \"import secrets; print(secrets.token_urlsafe(32))\"` 生成"
            )
        if self.environment == "development" and not self.jwt_secret_key:
            self.jwt_secret_key = secrets.token_urlsafe(32)
            logger.warning("开发模式: 已自动生成临时 JWT 密钥，生产环境必须显式配置")
        return self

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origins_list(self) -> list[str]:
        """解析 CORS 允许来源列表"""
        if self.environment == "development":
            return ["*"]
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        return origins

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()

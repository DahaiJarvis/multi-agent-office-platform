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

    # LLM 配置 - OpenAI
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model_flagship: str = Field(default="gpt-4o", alias="OPENAI_MODEL_FLAGSHIP")
    openai_model_standard: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL_STANDARD")

    # LLM 配置 - Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL")
    anthropic_model_flagship: str = Field(default="claude-3-5-sonnet-20241022", alias="ANTHROPIC_MODEL_FLAGSHIP")
    anthropic_model_economy: str = Field(default="claude-3-haiku-20240307", alias="ANTHROPIC_MODEL_ECONOMY")

    # LLM 配置 - 本地模型
    local_llm_base_url: str = Field(default="", alias="LOCAL_LLM_BASE_URL")
    local_llm_model: str = Field(default="", alias="LOCAL_LLM_MODEL")

    # LLM 路由策略
    llm_route_strategy: str = Field(default="priority", alias="LLM_ROUTE_STRATEGY")

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

    # SSO 企业身份集成
    sso_enabled: bool = Field(default=False, alias="SSO_ENABLED")

    sso_entra_id_enabled: bool = Field(default=False, alias="SSO_ENTRA_ID_ENABLED")
    sso_entra_id_client_id: str = Field(default="", alias="SSO_ENTRA_ID_CLIENT_ID")
    sso_entra_id_client_secret: str = Field(default="", alias="SSO_ENTRA_ID_CLIENT_SECRET")
    sso_entra_id_tenant_id: str = Field(default="common", alias="SSO_ENTRA_ID_TENANT_ID")
    sso_entra_id_redirect_uri: str = Field(default="", alias="SSO_ENTRA_ID_REDIRECT_URI")

    sso_okta_enabled: bool = Field(default=False, alias="SSO_OKTA_ENABLED")
    sso_okta_client_id: str = Field(default="", alias="SSO_OKTA_CLIENT_ID")
    sso_okta_client_secret: str = Field(default="", alias="SSO_OKTA_CLIENT_SECRET")
    sso_okta_domain: str = Field(default="", alias="SSO_OKTA_DOMAIN")
    sso_okta_redirect_uri: str = Field(default="", alias="SSO_OKTA_REDIRECT_URI")

    sso_wecom_enabled: bool = Field(default=False, alias="SSO_WECOM_ENABLED")
    sso_wecom_corp_id: str = Field(default="", alias="SSO_WECOM_CORP_ID")
    sso_wecom_agent_id: str = Field(default="", alias="SSO_WECOM_AGENT_ID")
    sso_wecom_secret: str = Field(default="", alias="SSO_WECOM_SECRET")
    sso_wecom_redirect_uri: str = Field(default="", alias="SSO_WECOM_REDIRECT_URI")

    sso_dingtalk_enabled: bool = Field(default=False, alias="SSO_DINGTALK_ENABLED")
    sso_dingtalk_client_id: str = Field(default="", alias="SSO_DINGTALK_CLIENT_ID")
    sso_dingtalk_client_secret: str = Field(default="", alias="SSO_DINGTALK_CLIENT_SECRET")
    sso_dingtalk_redirect_uri: str = Field(default="", alias="SSO_DINGTALK_REDIRECT_URI")

    sso_feishu_enabled: bool = Field(default=False, alias="SSO_FEISHU_ENABLED")
    sso_feishu_app_id: str = Field(default="", alias="SSO_FEISHU_APP_ID")
    sso_feishu_app_secret: str = Field(default="", alias="SSO_FEISHU_APP_SECRET")
    sso_feishu_redirect_uri: str = Field(default="", alias="SSO_FEISHU_REDIRECT_URI")

    # 静态数据加密
    encryption_enabled: bool = Field(default=True, alias="ENCRYPTION_ENABLED")
    encryption_key_provider: str = Field(default="auto", alias="ENCRYPTION_KEY_PROVIDER")
    encryption_key_file: str = Field(default="", alias="ENCRYPTION_KEY_FILE")
    encryption_master_key: str = Field(default="", alias="ENCRYPTION_MASTER_KEY")

    # 数据驻留控制
    data_residency_region: str = Field(default="cn-north", alias="DATA_RESIDENCY_REGION")
    data_residency_enforced: bool = Field(default=True, alias="DATA_RESIDENCY_ENFORCED")

    # 多租户
    multi_tenant_enabled: bool = Field(default=False, alias="MULTI_TENANT_ENABLED")
    tenant_default_isolation: str = Field(default="row", alias="TENANT_DEFAULT_ISOLATION")
    tenant_default_region: str = Field(default="cn-north", alias="TENANT_DEFAULT_REGION")

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

    @property
    def sso_provider_configs(self) -> dict:
        """获取所有已启用的 SSO 提供者配置"""
        configs = {}
        if self.sso_entra_id_enabled and self.sso_entra_id_client_id:
            configs["entra_id"] = {
                "client_id": self.sso_entra_id_client_id,
                "client_secret": self.sso_entra_id_client_secret,
                "tenant_id": self.sso_entra_id_tenant_id,
                "redirect_uri": self.sso_entra_id_redirect_uri,
                "scopes": ["openid", "profile", "email", "User.Read"],
            }
        if self.sso_okta_enabled and self.sso_okta_client_id:
            configs["okta"] = {
                "client_id": self.sso_okta_client_id,
                "client_secret": self.sso_okta_client_secret,
                "domain": self.sso_okta_domain,
                "redirect_uri": self.sso_okta_redirect_uri,
                "scopes": ["openid", "profile", "email"],
            }
        if self.sso_wecom_enabled and self.sso_wecom_corp_id:
            configs["wecom"] = {
                "corp_id": self.sso_wecom_corp_id,
                "agent_id": self.sso_wecom_agent_id,
                "client_id": self.sso_wecom_corp_id,
                "client_secret": self.sso_wecom_secret,
                "redirect_uri": self.sso_wecom_redirect_uri,
                "scopes": ["snsapi_privateinfo"],
            }
        if self.sso_dingtalk_enabled and self.sso_dingtalk_client_id:
            configs["dingtalk"] = {
                "client_id": self.sso_dingtalk_client_id,
                "client_secret": self.sso_dingtalk_client_secret,
                "redirect_uri": self.sso_dingtalk_redirect_uri,
                "scopes": ["openid", "corpid"],
            }
        if self.sso_feishu_enabled and self.sso_feishu_app_id:
            configs["feishu"] = {
                "client_id": self.sso_feishu_app_id,
                "client_secret": self.sso_feishu_app_secret,
                "redirect_uri": self.sso_feishu_redirect_uri,
                "scopes": [],
            }
        return configs


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()

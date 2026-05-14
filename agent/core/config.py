"""全局配置管理

使用 pydantic-settings 从环境变量加载配置，支持 .env 文件。
所有配置项通过 Field 定义默认值和环境变量别名。

配置分类：
- LLM 配置：多供应商 API 密钥和模型参数
- 数据库配置：PostgreSQL / Redis 连接参数
- 可观测性配置：Langfuse / OpenTelemetry
- API 服务配置：端口/Worker/CORS
- 安全配置：JWT / SSO / 加密
- 多租户配置：隔离策略/区域
- 限流配置：QPS/突发量
- MCP 配置：注册中心/IDA 集成
"""

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

    # 嵌入模型配置
    embedding_api_url: str = Field(default="", alias="EMBEDDING_API_URL")
    embedding_model: str = Field(default="text-embedding-v3", alias="EMBEDDING_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

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
    jwt_algorithm: str = Field(default="RS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")
    # RSA 非对称密钥（RS256 签名）
    # 私钥用于签发 Token，公钥用于验证 Token
    # 开发模式下未配置时自动生成临时 RSA 密钥对
    jwt_private_key: str = Field(default="", alias="JWT_PRIVATE_KEY")
    jwt_public_key: str = Field(default="", alias="JWT_PUBLIC_KEY")
    # 兼容旧配置：jwt_secret_key 作为 fallback，仅在 HS256 模式下使用
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")

    # OIDC 标准配置（统一身份源）
    # Token 签发者标识，子系统验证 Token 时校验此值
    jwt_issuer: str = Field(default="multi-agent-platform", alias="JWT_ISSUER")
    # Token 受众列表（逗号分隔），包含所有授权访问的子系统标识
    # 例如: "platform,ida-service" 表示 Token 可被主平台和 IDA 服务验证
    jwt_audiences: str = Field(default="platform,ida-service", alias="JWT_AUDIENCES")

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

    # 工具执行边界
    tool_execution_timeout: int = Field(default=10, description="工具调用超时秒数")
    tool_max_retries: int = Field(default=1, description="工具调用最大重试次数")
    tool_retry_backoff: float = Field(default=0.5, description="工具调用重试退避基数(秒)")
    tool_daily_quota: int = Field(default=1000, description="单工具每日调用配额")

    # 执行控制
    execution_max_runtime: int = Field(default=600, description="任务执行最大运行时间(秒)")
    execution_llm_timeout: int = Field(default=30, description="单轮LLM调用超时(秒)")
    execution_max_retries: int = Field(default=2, description="任务执行最大重试次数")
    execution_compaction_threshold: int = Field(default=80000, description="上下文压缩Token阈值")
    execution_stream_idle_timeout: int = Field(default=120, description="流式执行chunk间隔超时(秒)")

    # MCP Registry
    mcp_registry_url: str = Field(
        default="http://localhost:9099", alias="MCP_REGISTRY_URL"
    )

    # 智能文档助手集成配置
    # 本地开发：后端 5000，MCP Server 9010
    # Docker 部署：通过环境变量 IDA_BACKEND_URL / IDA_MCP_SSE_URL 覆盖
    ida_backend_url: str = Field(default="http://localhost:5000", alias="IDA_BACKEND_URL")
    ida_mcp_sse_url: str = Field(default="http://localhost:9010/sse", alias="IDA_MCP_SSE_URL")
    # IDA REST API 路径前缀，启动时自动探测，也可手动配置
    # 自动探测逻辑：依次尝试 /api/v1/ 和 /api/，选择可用的版本
    # 手动配置示例：/api/v1 或 /api（不带尾部斜杠）
    ida_api_prefix: str = Field(default="", alias="IDA_API_PREFIX")
    # SSE 连接认证密钥，用于 MCP 客户端连接 IDA SSE 端点时的 API Key 验证
    mcp_api_key: str = Field(default="", alias="MCP_API_KEY")
    # 映射 Token 有效期（秒），用于主平台向 IDA 签发的 RSA-JWT Token
    # 取值范围: 60~86400，默认 3600（1 小时）
    ida_token_ttl_seconds: int = Field(default=3600, alias="IDA_TOKEN_TTL_SECONDS")

    # IDA 认证模式：legacy(映射Token) / direct(透传Token)
    # legacy: 使用 RSA 私钥签发映射 Token，IDA 使用 RSA 公钥验证（兼容旧系统）
    # direct: 直接透传主平台 Token，IDA 通过 JWKS 端点验证（推荐，统一身份源）
    # 改造期间使用 legacy 模式保持兼容，IDA 改造完成后切换为 direct 模式
    ida_auth_mode: str = Field(default="legacy", alias="IDA_AUTH_MODE")

    # RSA 非对称密钥 JWT 认证配置
    platform_jwt_private_key: str = Field(default="", alias="PLATFORM_JWT_PRIVATE_KEY")
    platform_jwt_issuer: str = Field(default="multi-agent-platform", alias="PLATFORM_JWT_ISSUER")
    platform_jwt_audience: str = Field(default="ida-service", alias="PLATFORM_JWT_AUDIENCE")
    platform_role_mapping: str = Field(
        default='{"platform_admin":"admin","platform_user":"user","platform_viewer":"viewer"}',
        alias="PLATFORM_ROLE_MAPPING",
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @model_validator(mode="after")
    def _validate_security_config(self) -> "Settings":
        """安全配置校验：RS256 密钥对校验与自动生成；RSA 私钥格式规范化"""
        # JWT 私钥/公钥 PEM 格式规范化（.env 中 \n 字面量转实际换行）
        self.jwt_private_key = self._normalize_pem_key(self.jwt_private_key, "JWT_PRIVATE_KEY")
        self.jwt_public_key = self._normalize_pem_key(self.jwt_public_key, "JWT_PUBLIC_KEY")

        if self.jwt_algorithm == "RS256":
            # RS256 模式：必须有私钥和公钥
            if not self.jwt_private_key or not self.jwt_public_key:
                if self.environment == "development":
                    self._auto_generate_rsa_keypair()
                else:
                    raise ValueError(
                        "生产环境使用 RS256 算法必须配置 JWT_PRIVATE_KEY 和 JWT_PUBLIC_KEY 环境变量，"
                        "可通过 `python -c \"from cryptography.hazmat.primitives.asymmetric import rsa; "
                        "from cryptography.hazmat.primitives import serialization; "
                        "key = rsa.generate_private_key(public_exponent=65537, key_size=2048); "
                        "print(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()); "
                        "print(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())\"` 生成"
                    )
        else:
            # HS256 等对称算法：必须有 jwt_secret_key
            if not self.jwt_secret_key:
                if self.environment == "development":
                    self.jwt_secret_key = secrets.token_urlsafe(32)
                    logger.warning("开发模式: 已自动生成临时 JWT 密钥，生产环境必须显式配置")
                else:
                    raise ValueError(
                        "生产环境必须设置 JWT_SECRET_KEY 环境变量，"
                        "可通过 `python -c \"import secrets; print(secrets.token_urlsafe(32))\"` 生成"
                    )

        # platform_jwt_private_key 格式规范化
        if self.platform_jwt_private_key:
            self.platform_jwt_private_key = self._normalize_pem_key(
                self.platform_jwt_private_key, "PLATFORM_JWT_PRIVATE_KEY"
            )
            if self.platform_jwt_private_key and not self.platform_jwt_private_key.startswith("-----BEGIN"):
                logger.warning("PLATFORM_JWT_PRIVATE_KEY 格式无效: 缺少 PEM 头标记")
                self.platform_jwt_private_key = ""

        # IDA 认证模式校验
        if self.ida_auth_mode not in ("legacy", "direct"):
            logger.warning("IDA_AUTH_MODE 配置无效: %s，使用默认值 legacy", self.ida_auth_mode)
            self.ida_auth_mode = "legacy"

        # direct 模式下校验 OIDC 配置完整性
        if self.ida_auth_mode == "direct":
            if not self.jwt_issuer:
                logger.warning("IDA_AUTH_MODE=direct 但 JWT_ISSUER 未配置，使用默认值")
                self.jwt_issuer = "multi-agent-platform"
            if not self.jwt_audiences:
                logger.warning("IDA_AUTH_MODE=direct 但 JWT_AUDIENCES 未配置，使用默认值")
                self.jwt_audiences = "platform,ida-service"
            if self.jwt_algorithm != "RS256":
                logger.warning("IDA_AUTH_MODE=direct 建议使用 RS256 算法，当前: %s", self.jwt_algorithm)

        return self

    @staticmethod
    def _normalize_pem_key(key: str, name: str) -> str:
        """PEM 密钥格式规范化

        .env 文件中的 PEM 密钥通常使用 \\n 字面量表示换行，
        pydantic-settings 不会自动转换，需要在此处处理。

        Args:
            key: 原始密钥字符串
            name: 配置项名称（用于日志）

        Returns:
            规范化后的 PEM 密钥字符串
        """
        if not key:
            return ""
        key = key.strip()
        if "\\n" in key and "\n" not in key:
            key = key.replace("\\n", "\n")
        return key

    def _auto_generate_rsa_keypair(self) -> None:
        """开发模式自动生成临时 RSA 密钥对

        生成 2048 位 RSA 密钥对，用于 RS256 签名。
        仅在开发模式下使用，生产环境必须显式配置。
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization

            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            self.jwt_private_key = private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode("utf-8")
            self.jwt_public_key = private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
            logger.warning("开发模式: 已自动生成临时 RSA 密钥对，生产环境必须显式配置 JWT_PRIVATE_KEY 和 JWT_PUBLIC_KEY")
        except ImportError:
            logger.warning("cryptography 库未安装，降级使用 HS256 对称签名")
            self.jwt_algorithm = "HS256"
            if not self.jwt_secret_key:
                self.jwt_secret_key = secrets.token_urlsafe(32)

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

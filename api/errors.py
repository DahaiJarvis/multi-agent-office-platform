"""统一错误码体系

定义全局错误码、错误异常类和全局异常处理器，
确保所有 API 返回一致的错误响应格式。

错误码规范: AABBB
  AA: 模块编码 (10=通用, 20=认证, 30=Agent, 40=会话, 50=MCP, 60=限流)
  BBB: 具体错误编号

响应格式:
  {
    "error_code": "20001",
    "error": "unauthorized",
    "message": "Token 无效或已过期",
    "request_id": "xxx"
  }
"""

import logging
from enum import Enum

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    """统一错误码"""

    # 10xxx - 通用错误
    INTERNAL_ERROR = "10001"
    INVALID_PARAMETER = "10002"
    NOT_FOUND = "10003"
    METHOD_NOT_ALLOWED = "10004"
    CONFLICT = "10005"
    SERVICE_UNAVAILABLE = "10006"

    # 20xxx - 认证与授权
    UNAUTHORIZED = "20001"
    TOKEN_EXPIRED = "20002"
    TOKEN_INVALID = "20003"
    TOKEN_REVOKED = "20004"
    LOGIN_FAILED = "20005"
    LOGIN_RATE_LIMITED = "20006"
    PERMISSION_DENIED = "20007"
    REFRESH_TOKEN_INVALID = "20008"
    SSO_PROVIDER_NOT_FOUND = "20009"
    SSO_STATE_INVALID = "20010"
    SSO_CALLBACK_FAILED = "20011"
    SSO_USER_MAPPING_FAILED = "20012"

    # 30xxx - Agent 相关
    AGENT_NOT_FOUND = "30001"
    AGENT_EXECUTION_FAILED = "30002"
    INTENT_CLASSIFICATION_FAILED = "30003"
    TEAM_CREATION_FAILED = "30004"
    CLARIFICATION_NEEDED = "30005"
    GUARDRAIL_BLOCKED = "30006"
    REVIEW_REQUIRED = "30007"

    # 40xxx - 会话相关
    SESSION_NOT_FOUND = "40001"
    SESSION_EXPIRED = "40002"
    SESSION_CREATE_FAILED = "40003"

    # 50xxx - MCP 相关
    MCP_SERVICE_UNAVAILABLE = "50001"
    MCP_TOOL_CALL_FAILED = "50002"
    MCP_SERVICE_NOT_FOUND = "50003"
    MCP_REGISTRY_UNAVAILABLE = "50004"

    # 60xxx - 限流与降级
    RATE_LIMITED = "60001"
    USER_RATE_LIMITED = "60002"
    SYSTEM_DEGRADED = "60003"

    # 70xxx - 数据与存储
    DATABASE_ERROR = "70001"
    REDIS_ERROR = "70002"
    CACHE_MISS = "70003"

    # 80xxx - Skills 相关
    SKILL_NOT_FOUND = "80001"
    SKILL_PARSE_ERROR = "80002"
    SKILL_VALIDATION_ERROR = "80003"
    SKILL_ALREADY_EXISTS = "80004"
    SKILL_BIND_FAILED = "80005"

    # 81xxx - 原生工具相关
    NATIVE_TOOL_NOT_FOUND = "81001"
    NATIVE_TOOL_DISABLED = "81002"
    NATIVE_TOOL_EXECUTION_FAILED = "81003"


class ErrorCodeMeta:
    """错误码元信息注册表"""

    _meta: dict[str, dict] = {
        ErrorCode.INTERNAL_ERROR: {"error": "internal_error", "status": 500, "message": "内部服务错误"},
        ErrorCode.INVALID_PARAMETER: {"error": "invalid_parameter", "status": 400, "message": "参数校验失败"},
        ErrorCode.NOT_FOUND: {"error": "not_found", "status": 404, "message": "资源不存在"},
        ErrorCode.METHOD_NOT_ALLOWED: {"error": "method_not_allowed", "status": 405, "message": "请求方法不允许"},
        ErrorCode.CONFLICT: {"error": "conflict", "status": 409, "message": "资源冲突"},
        ErrorCode.SERVICE_UNAVAILABLE: {"error": "service_unavailable", "status": 503, "message": "服务暂不可用"},

        ErrorCode.UNAUTHORIZED: {"error": "unauthorized", "status": 401, "message": "未授权"},
        ErrorCode.TOKEN_EXPIRED: {"error": "token_expired", "status": 401, "message": "Token 已过期"},
        ErrorCode.TOKEN_INVALID: {"error": "token_invalid", "status": 401, "message": "Token 无效"},
        ErrorCode.TOKEN_REVOKED: {"error": "token_revoked", "status": 401, "message": "Token 已撤销"},
        ErrorCode.LOGIN_FAILED: {"error": "login_failed", "status": 401, "message": "登录失败"},
        ErrorCode.LOGIN_RATE_LIMITED: {"error": "login_rate_limited", "status": 429, "message": "登录尝试过于频繁"},
        ErrorCode.PERMISSION_DENIED: {"error": "permission_denied", "status": 403, "message": "权限不足"},
        ErrorCode.REFRESH_TOKEN_INVALID: {"error": "refresh_token_invalid", "status": 401, "message": "刷新令牌无效"},
        ErrorCode.SSO_PROVIDER_NOT_FOUND: {"error": "sso_provider_not_found", "status": 400, "message": "SSO 提供者未注册"},
        ErrorCode.SSO_STATE_INVALID: {"error": "sso_state_invalid", "status": 400, "message": "SSO 授权状态无效或已过期"},
        ErrorCode.SSO_CALLBACK_FAILED: {"error": "sso_callback_failed", "status": 401, "message": "SSO 授权回调失败"},
        ErrorCode.SSO_USER_MAPPING_FAILED: {"error": "sso_user_mapping_failed", "status": 500, "message": "SSO 用户映射失败"},

        ErrorCode.AGENT_NOT_FOUND: {"error": "agent_not_found", "status": 404, "message": "Agent 不存在"},
        ErrorCode.AGENT_EXECUTION_FAILED: {"error": "agent_execution_failed", "status": 500, "message": "Agent 执行失败"},
        ErrorCode.INTENT_CLASSIFICATION_FAILED: {"error": "intent_classification_failed", "status": 500, "message": "意图分类失败"},
        ErrorCode.TEAM_CREATION_FAILED: {"error": "team_creation_failed", "status": 500, "message": "团队创建失败"},
        ErrorCode.CLARIFICATION_NEEDED: {"error": "clarification_needed", "status": 200, "message": "需要用户澄清"},
        ErrorCode.GUARDRAIL_BLOCKED: {"error": "guardrail_blocked", "status": 403, "message": "安全护栏拦截"},
        ErrorCode.REVIEW_REQUIRED: {"error": "review_required", "status": 202, "message": "需要人工审核"},

        ErrorCode.SESSION_NOT_FOUND: {"error": "session_not_found", "status": 404, "message": "会话不存在"},
        ErrorCode.SESSION_EXPIRED: {"error": "session_expired", "status": 410, "message": "会话已过期"},
        ErrorCode.SESSION_CREATE_FAILED: {"error": "session_create_failed", "status": 500, "message": "会话创建失败"},

        ErrorCode.MCP_SERVICE_UNAVAILABLE: {"error": "mcp_service_unavailable", "status": 503, "message": "MCP 服务不可用"},
        ErrorCode.MCP_TOOL_CALL_FAILED: {"error": "mcp_tool_call_failed", "status": 500, "message": "MCP 工具调用失败"},
        ErrorCode.MCP_SERVICE_NOT_FOUND: {"error": "mcp_service_not_found", "status": 404, "message": "MCP 服务未注册"},
        ErrorCode.MCP_REGISTRY_UNAVAILABLE: {"error": "mcp_registry_unavailable", "status": 503, "message": "MCP 注册中心不可用"},

        ErrorCode.RATE_LIMITED: {"error": "rate_limited", "status": 429, "message": "请求过于频繁"},
        ErrorCode.USER_RATE_LIMITED: {"error": "user_rate_limited", "status": 429, "message": "用户请求频率超限"},
        ErrorCode.SYSTEM_DEGRADED: {"error": "system_degraded", "status": 503, "message": "系统降级中"},

        ErrorCode.DATABASE_ERROR: {"error": "database_error", "status": 500, "message": "数据库错误"},
        ErrorCode.REDIS_ERROR: {"error": "redis_error", "status": 500, "message": "缓存服务错误"},
        ErrorCode.CACHE_MISS: {"error": "cache_miss", "status": 404, "message": "缓存未命中"},

        ErrorCode.SKILL_NOT_FOUND: {"error": "skill_not_found", "status": 404, "message": "Skill 不存在"},
        ErrorCode.SKILL_PARSE_ERROR: {"error": "skill_parse_error", "status": 400, "message": "SKILL.md 解析失败"},
        ErrorCode.SKILL_VALIDATION_ERROR: {"error": "skill_validation_error", "status": 400, "message": "Skill 校验失败"},
        ErrorCode.SKILL_ALREADY_EXISTS: {"error": "skill_already_exists", "status": 409, "message": "Skill 已存在"},
        ErrorCode.SKILL_BIND_FAILED: {"error": "skill_bind_failed", "status": 400, "message": "Skill 绑定失败"},

        ErrorCode.NATIVE_TOOL_NOT_FOUND: {"error": "native_tool_not_found", "status": 404, "message": "原生工具不存在"},
        ErrorCode.NATIVE_TOOL_DISABLED: {"error": "native_tool_disabled", "status": 403, "message": "原生工具已禁用"},
        ErrorCode.NATIVE_TOOL_EXECUTION_FAILED: {"error": "native_tool_execution_failed", "status": 500, "message": "原生工具执行失败"},
    }

    @classmethod
    def get(cls, code: str) -> dict:
        return cls._meta.get(code, {"error": "unknown", "status": 500, "message": "未知错误"})


class ErrorResponse(BaseModel):
    """统一错误响应"""

    error_code: str
    error: str
    message: str
    request_id: str | None = None


class AppException(Exception):
    """应用统一异常

    所有业务异常均应抛出此异常，由全局异常处理器统一处理。

    Args:
        error_code: 错误码
        message: 错误详情（覆盖默认消息）
        detail: 附加详情
    """

    def __init__(
        self,
        error_code: ErrorCode,
        message: str | None = None,
        detail: str | None = None,
    ):
        self.error_code = error_code
        self.message = message
        self.detail = detail
        meta = ErrorCodeMeta.get(error_code.value)
        self.error = meta["error"]
        self.status_code = meta["status"]
        self.default_message = meta["message"]
        super().__init__(message or self.default_message)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """AppException 全局异常处理器"""
    request_id = getattr(request.state, "request_id", None)

    response_body = ErrorResponse(
        error_code=exc.error_code.value,
        error=exc.error,
        message=exc.message or exc.default_message,
        request_id=request_id,
    )

    if exc.status_code >= 500:
        logger.error(
            "服务端错误: code=%s error=%s detail=%s request_id=%s",
            exc.error_code.value,
            exc.error,
            exc.detail,
            request_id,
        )
    else:
        logger.warning(
            "客户端错误: code=%s error=%s message=%s request_id=%s",
            exc.error_code.value,
            exc.error,
            exc.message,
            request_id,
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=response_body.model_dump(exclude_none=True),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常的全局兜底处理器"""
    request_id = getattr(request.state, "request_id", None)

    logger.error(
        "未捕获异常: %s request_id=%s path=%s",
        str(exc),
        request_id,
        request.url.path,
        exc_info=True,
    )

    response_body = ErrorResponse(
        error_code=ErrorCode.INTERNAL_ERROR.value,
        error="internal_error",
        message="内部服务错误，请稍后重试",
        request_id=request_id,
    )

    return JSONResponse(
        status_code=500,
        content=response_body.model_dump(exclude_none=True),
    )

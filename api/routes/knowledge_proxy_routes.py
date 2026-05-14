"""知识库 REST 代理路由

将前端请求转发到智能文档助手(IDA) REST API，实现跨系统调用。

认证流程（双模式支持）：

  direct 模式（IDA_AUTH_MODE=direct，推荐）：
  1. 前端请求携带主平台 JWT Token
  2. AuthMiddleware 解析主平台 JWT，提取 user_id 和 user_roles
  3. 直接透传主平台 Token 给 IDA
  4. IDA 通过 JWKS 端点验证主平台 Token

  legacy 模式（IDA_AUTH_MODE=legacy，兼容旧系统）：
  1. 前端请求携带主平台 JWT Token
  2. AuthMiddleware 解析主平台 JWT，提取 user_id 和 user_roles
  3. 本模块使用 RSA 私钥签发映射 Token（包含 IDA 角色映射）
  4. 映射 Token 发送给 IDA，IDA 使用 RSA 公钥验证

设计要点：
- 使用模块级共享 httpx.AsyncClient 避免连接泄漏
- 统一错误处理，将 IDA 错误响应转换为平台标准格式
- Token 有效期通过 config.py 的 ida_token_ttl_seconds 配置
- 请求体通过 Pydantic 模型校验，防止非法参数透传
- SSE 流式代理错误事件格式与前端解析逻辑对齐
- 双模式支持确保 IDA 改造期间主平台可独立部署
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from agent.core.config import get_settings
from api.errors import AppException, ErrorCode
from api.models.request import (
    CreateKnowledgeBaseRequest,
    UpdateKnowledgeBaseRequest,
    QAAskRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

settings = get_settings()

# 模块级共享 HTTP 客户端，避免每次请求创建新连接
# 生命周期由 FastAPI lifespan 管理（通过 close_knowledge_proxy_client 关闭）
_shared_client: httpx.AsyncClient | None = None

# IDA API 路径前缀缓存（启动时探测，运行时使用）
_ida_api_prefix: str = ""

# IDA 连接状态缓存，避免短时间内重复探测
_ida_last_connect_ok: bool = False

# IDA API 版本探测候选列表（按优先级排序，优先使用高版本）
_IDA_PREFIX_CANDIDATES = ["/api/v1", "/api"]


async def detect_ida_api_prefix() -> str:
    """自动探测 IDA REST API 路径前缀

    启动时依次尝试候选路径，找到第一个可用的版本。
    探测逻辑：
      1. 如果配置了 IDA_API_PREFIX，直接使用
      2. 否则依次尝试 /api/v1 和 /api
      3. 通过 HEAD/GET 请求检测路径是否可达（非 404）
      4. 探测失败时使用默认值 /api/v1

    Returns:
        IDA API 路径前缀，如 /api/v1 或 /api
    """
    global _ida_api_prefix, _ida_last_connect_ok

    if settings.ida_api_prefix:
        prefix = settings.ida_api_prefix.rstrip("/")
        _ida_api_prefix = prefix
        logger.info("IDA API 前缀使用配置值: %s", prefix)
        return prefix

    client = get_knowledge_proxy_client()

    for prefix in _IDA_PREFIX_CANDIDATES:
        try:
            probe_url = f"{settings.ida_backend_url}{prefix}/knowledge-bases"
            response = await client.get(
                probe_url,
                params={"page": 1, "per_page": 1},
                timeout=5.0,
            )
            if response.status_code != 404:
                _ida_api_prefix = prefix
                _ida_last_connect_ok = True
                logger.info("IDA API 前缀自动探测成功: %s (status=%d)", prefix, response.status_code)
                return prefix
        except Exception:
            continue

    _ida_api_prefix = "/api/v1"
    _ida_last_connect_ok = False
    logger.warning("IDA API 前缀自动探测失败，使用默认值: /api/v1")
    return _ida_api_prefix


def get_ida_api_prefix() -> str:
    """获取 IDA API 路径前缀

    优先使用已探测的缓存值，未探测时返回默认值。

    Returns:
        IDA API 路径前缀
    """
    return _ida_api_prefix or settings.ida_api_prefix.rstrip("/") or "/api/v1"


def get_knowledge_proxy_client() -> httpx.AsyncClient:
    """获取共享 HTTP 客户端单例

    使用懒初始化模式，首次调用时创建客户端。
    连接池大小和超时通过 httpx 默认配置管理。

    Returns:
        httpx.AsyncClient 共享实例
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        )
    return _shared_client


async def close_knowledge_proxy_client() -> None:
    """关闭共享 HTTP 客户端，释放连接池资源

    应在应用关闭时调用（lifespan shutdown 阶段）。
    """
    global _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
        logger.info("知识库代理 HTTP 客户端已关闭")


def _get_token_ttl() -> int:
    """获取映射 Token 有效期（秒）

    从 config.py 的 ida_token_ttl_seconds 读取，
    未配置或无效时使用默认值 3600 秒（1 小时）。

    Returns:
        Token 有效期秒数，最小 60 秒，最大 86400 秒（24 小时）
    """
    ttl = settings.ida_token_ttl_seconds
    if ttl < 60:
        return 60
    if ttl > 86400:
        return 86400
    return ttl


def _get_role_mapping() -> dict[str, str]:
    """获取角色映射配置

    从环境变量 PLATFORM_ROLE_MAPPING 解析角色映射表。
    映射方向：主平台角色 -> IDA 角色。
    解析失败时使用默认映射。

    Returns:
        角色映射字典，key 为主平台角色，value 为 IDA 角色
        例如: {"platform_admin": "admin", "platform_user": "user"}
    """
    try:
        return json.loads(settings.platform_role_mapping)
    except (json.JSONDecodeError, TypeError):
        return {
            "platform_admin": "admin",
            "platform_user": "user",
            "platform_viewer": "viewer",
        }


def _map_platform_role(user_roles: list[str]) -> str:
    """将主平台角色映射为 IDA 角色

    遍历用户角色列表，按优先级（admin > user > viewer）匹配映射表。
    未匹配的角色默认映射为 IDA 的 "user" 角色。

    角色优先级逻辑：
    1. 遍历用户所有角色，查找在映射表中的匹配项
    2. 如果同时拥有多个角色，优先返回高权限角色
    3. 无匹配角色时返回默认的 "user"

    Args:
        user_roles: 主平台用户角色列表，如 ["platform_admin", "platform_user"]

    Returns:
        IDA 角色标识，如 "admin"、"user"、"viewer"
    """
    role_mapping = _get_role_mapping()
    if not user_roles:
        return "user"

    # 按优先级遍历：admin > user > viewer
    priority_order = ["platform_admin", "platform_user", "platform_viewer"]
    for platform_role in priority_order:
        if platform_role in user_roles:
            return role_mapping.get(platform_role, "user")

    # 尝试直接匹配
    for role in user_roles:
        if role in role_mapping:
            return role_mapping[role]

    return "user"


def _build_mapped_token(user_id: str, user_roles: list[str]) -> str:
    """使用 RSA 私钥签发映射 Token

    映射 Token 用于主平台向 IDA 的跨系统认证。
    Token 包含 aud(受众)和 iss(签发者)校验，防止 Token 滥用。
    IDA 使用 RSA 公钥验证此 Token。

    开发模式降级：
    当 RSA 私钥未配置或格式无效，且 environment == "development" 时，
    使用 HS256 对称签名 + mcp_api_key 作为共享密钥签发 Token。
    生产环境必须配置有效的 RSA 私钥。

    Token Payload 结构：
    - sub: 用户标识
    - role: 映射后的 IDA 角色
    - aud: 受众（IDA 服务标识）
    - iss: 签发者（主平台标识）
    - exp: 过期时间
    - iat: 签发时间

    Args:
        user_id: 主平台用户 ID
        user_roles: 主平台用户角色列表

    Returns:
        JWT Token 字符串（RS256 或 HS256 签名）

    Raises:
        AppException: 生产环境下 RSA 私钥未配置或格式无效时抛出 500 错误
    """
    platform_role = _map_platform_role(user_roles)
    now = datetime.now(timezone.utc)
    ttl = _get_token_ttl()
    payload = {
        "sub": user_id,
        "role": platform_role,
        "aud": settings.platform_jwt_audience,
        "iss": settings.platform_jwt_issuer,
        "exp": now + timedelta(seconds=ttl),
        "iat": now,
    }

    # 尝试使用 RS256 签名
    if settings.platform_jwt_private_key:
        try:
            return jwt.encode(
                payload,
                settings.platform_jwt_private_key,
                algorithm="RS256",
            )
        except jwt.InvalidKeyError as e:
            logger.warning("RSA 私钥格式无效: %s", str(e))
            if settings.environment != "development":
                logger.error("生产环境 RSA 私钥格式无效，无法签发映射 Token")
                raise AppException(
                    ErrorCode.INTERNAL_ERROR,
                    message="智能文档助手认证配置错误，请联系管理员检查 RSA 密钥配置",
                )
        except Exception as e:
            logger.warning("RS256 签名失败: %s", str(e))
            if settings.environment != "development":
                logger.error("生产环境签发映射 Token 失败: %s", str(e))
                raise AppException(
                    ErrorCode.INTERNAL_ERROR,
                    message="智能文档助手认证服务异常，请稍后重试",
                )

    # 开发模式降级：使用 HS256 对称签名
    if settings.environment == "development":
        shared_secret = settings.mcp_api_key
        if not shared_secret:
            logger.error("开发模式缺少 MCP_API_KEY 配置，无法签发映射 Token")
            raise AppException(
                ErrorCode.INTERNAL_ERROR,
                message="开发模式缺少 MCP_API_KEY 配置，请在环境变量中设置",
            )
        logger.info("开发模式：使用 HS256 对称签名签发映射 Token")
        return jwt.encode(payload, shared_secret, algorithm="HS256")

    # 生产环境且无有效 RSA 私钥
    logger.error("RSA 私钥未配置，无法签发映射 Token")
    raise AppException(
        ErrorCode.INTERNAL_ERROR,
        message="智能文档助手认证配置缺失，请联系管理员",
    )


async def _get_mapped_token(request: Request) -> str:
    """从请求上下文提取用户信息并签发映射 Token（legacy 模式）

    依赖 AuthMiddleware 在 request.state 上设置的 user_id 和 user_roles。
    此函数仅在 IDA_AUTH_MODE=legacy 时使用。

    Args:
        request: FastAPI 请求对象

    Returns:
        RS256 签名的 JWT Token 字符串

    Raises:
        AppException: 用户信息缺失时抛出 401 错误
    """
    user_id = getattr(request.state, "user_id", None)
    user_roles = getattr(request.state, "user_roles", None)

    if not user_id:
        raise AppException(
            ErrorCode.UNAUTHORIZED,
            message="需要登录后才能访问知识库功能",
        )

    return _build_mapped_token(user_id, user_roles or [])


async def _get_user_token(request: Request) -> str:
    """获取用于 IDA 认证的 Token（双模式支持）

    根据 IDA_AUTH_MODE 配置选择认证模式：
    - direct: 直接透传主平台 Token，IDA 通过 JWKS 验证
    - legacy: 签发映射 Token，IDA 使用 RSA 公钥验证

    direct 模式下，直接从请求头提取原始 Bearer Token 透传给 IDA，
    无需签发新 Token，消除映射层，实现统一身份源。
    IDA 通过主平台的 /.well-known/jwks.json 端点获取公钥验证 Token。

    legacy 模式下，保留原有的映射 Token 签发逻辑，
    确保 IDA 尚未完成改造时主平台可独立部署运行。

    Args:
        request: FastAPI 请求对象

    Returns:
        JWT Token 字符串

    Raises:
        AppException: 用户信息缺失或 Token 提取失败时抛出 401 错误
    """
    auth_mode = getattr(settings, "ida_auth_mode", "legacy") or "legacy"

    if auth_mode == "direct":
        # direct 模式：直接透传主平台 Token
        auth_header = request.headers.get("Authorization")
        token = auth_header.replace("Bearer ", "") if auth_header and auth_header.startswith("Bearer ") else ""

        if not token:
            raise AppException(
                ErrorCode.UNAUTHORIZED,
                message="需要登录后才能访问知识库功能",
            )

        logger.debug("使用 direct 模式透传主平台 Token")
        return token
    else:
        # legacy 模式：签发映射 Token
        logger.debug("使用 legacy 模式签发映射 Token")
        return await _get_mapped_token(request)


def _get_ida_headers(token: str) -> dict[str, str]:
    """构建 IDA 请求头

    包含 Bearer Token 认证和来源标识，用于 IDA 识别请求来源。

    direct 模式下，Token 为主平台原始 Token，IDA 通过 JWKS 验证；
    legacy 模式下，Token 为映射 Token，IDA 使用 RSA 公钥验证。
    X-Source 头保留用于日志追踪，不影响认证流程。

    Args:
        token: JWT Token（主平台原始 Token 或映射 Token）

    Returns:
        包含认证和来源标识的请求头字典
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Source": "platform-proxy",
    }


def _handle_ida_response(response: httpx.Response) -> Any:
    """统一处理 IDA 响应，将错误转换为平台标准格式

    IDA 返回 2xx 时直接透传响应体；
    IDA 返回 4xx/5xx 时转换为 AppException，避免前端收到非标准错误格式。

    Args:
        response: httpx 响应对象

    Returns:
        IDA 响应的 JSON 数据

    Raises:
        AppException: IDA 返回非 2xx 状态码时
    """
    if 200 <= response.status_code < 300:
        return response.json()

    # 尝试解析 IDA 错误响应体
    try:
        error_body = response.json()
        error_message = error_body.get("detail", error_body.get("message", str(error_body)))
    except Exception:
        error_message = f"IDA 服务返回错误: HTTP {response.status_code}"

    logger.warning(
        "IDA 请求失败: status=%d, path=%s, error=%s",
        response.status_code,
        response.url.path,
        error_message,
    )

    # 将 IDA 的 401/403 映射为平台对应状态码，其他统一为 502
    if response.status_code in (401, 403):
        raise AppException(
            ErrorCode.PERMISSION_DENIED,
            message=f"知识库服务认证失败: {error_message}",
        )

    raise AppException(
        ErrorCode.SERVICE_UNAVAILABLE,
        message=f"知识库服务异常: {error_message}",
    )


async def _ida_request(method: str, path: str, token: str, **kwargs) -> Any:
    """向 IDA 发送请求并统一处理响应与网络异常

    封装 httpx 请求调用，统一处理连接失败、超时等网络异常，
    避免每个端点函数重复编写 try/except。
    path 参数只需传资源路径，API 前缀由 get_ida_api_prefix() 自动拼接。
    连接失败时自动重试一次（重新探测 API 前缀），支持 IDA 延迟启动场景。

    Args:
        method: HTTP 方法（GET/POST/PUT/DELETE）
        path: IDA API 资源路径，如 /knowledge-bases（不含 /api/v1 前缀）
        token: RSA-JWT 映射 Token
        **kwargs: 传递给 httpx 请求的额外参数（json/params/timeout 等）

    Returns:
        IDA 响应的 JSON 数据

    Raises:
        AppException: 网络异常或 IDA 返回错误时
    """
    return await _ida_request_with_retry(method, path, token, retry_on_connect=True, **kwargs)


async def _ida_request_with_retry(
    method: str, path: str, token: str, *, retry_on_connect: bool = True, **kwargs
) -> Any:
    """IDA 请求核心实现，支持连接失败时重试

    首次连接失败时，重新探测 IDA API 前缀并重试一次，
    解决 IDA 在主平台之后启动导致请求不可达的问题。

    Args:
        method: HTTP 方法
        path: IDA API 资源路径
        token: JWT Token
        retry_on_connect: 连接失败时是否重试
        **kwargs: 传递给 httpx 的额外参数

    Returns:
        IDA 响应的 JSON 数据

    Raises:
        AppException: 网络异常或 IDA 返回错误时
    """
    global _ida_last_connect_ok

    client = get_knowledge_proxy_client()
    prefix = get_ida_api_prefix()
    url = f"{settings.ida_backend_url}{prefix}{path}"
    headers = _get_ida_headers(token)

    try:
        response = await client.request(method, url, headers=headers, **kwargs)
        _ida_last_connect_ok = True
        return _handle_ida_response(response)
    except AppException:
        raise
    except httpx.ConnectError:
        if retry_on_connect:
            logger.warning("IDA 服务连接失败，尝试重新探测并重试: %s", url)
            try:
                new_prefix = await detect_ida_api_prefix()
                new_url = f"{settings.ida_backend_url}{new_prefix}{path}"
                response = await client.request(method, new_url, headers=headers, **kwargs)
                _ida_last_connect_ok = True
                return _handle_ida_response(response)
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except AppException:
                raise
            except Exception:
                pass
        _ida_last_connect_ok = False
        logger.error("IDA 服务连接失败: %s", url)
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            message="知识库服务连接失败，请确认服务是否正常运行",
        )
    except httpx.TimeoutException:
        logger.error("IDA 服务请求超时: %s", url)
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            message="知识库服务响应超时，请稍后重试",
        )
    except Exception as e:
        logger.error("IDA 请求异常: %s, error: %s", url, str(e))
        raise AppException(
            ErrorCode.SERVICE_UNAVAILABLE,
            message="知识库服务异常，请稍后重试",
        )


def _build_sse_error_event(error_message: str, error_code: str = "IDA_SERVICE_ERROR") -> str:
    """构建与前端 SSE 解析逻辑对齐的错误事件

    前端 chatStreamFetch 使用 JSON.parse(line.slice(6)) 解析 data 行，
    因此错误事件的 data 必须是合法 JSON，且包含 event 字段标识错误类型。

    事件格式：
        event: error\\n
        data: {"event":"error","error":"IDA_SERVICE_ERROR","message":"..."}\\n\\n

    Args:
        error_message: 错误描述信息
        error_code: 错误码标识

    Returns:
        格式化的 SSE 错误事件字符串
    """
    error_data = json.dumps(
        {"event": "error", "error": error_code, "message": error_message},
        ensure_ascii=False,
    )
    return f"event: error\ndata: {error_data}\n\n"


@router.get("/knowledge-bases", summary="列出知识库")
async def proxy_list_knowledge_bases(
    request: Request,
    page: int = 1,
    per_page: int = 20,
):
    """代理：获取知识库列表

    Args:
        request: FastAPI 请求对象
        page: 页码，从 1 开始
        per_page: 每页数量，默认 20

    Returns:
        IDA 知识库列表响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "GET", "/knowledge-bases", token,
        params={"page": page, "per_page": per_page},
    )


@router.post("/knowledge-bases", summary="创建知识库")
async def proxy_create_knowledge_base(
    request: Request,
    body: CreateKnowledgeBaseRequest,
):
    """代理：创建知识库

    使用 CreateKnowledgeBaseRequest 校验请求体，
    确保名称非空、访问级别合法，防止非法参数透传到 IDA。

    Args:
        request: FastAPI 请求对象
        body: Pydantic 校验后的创建知识库请求体

    Returns:
        IDA 创建知识库响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "POST", "/knowledge-bases", token,
        json=body.model_dump(exclude_none=True),
    )


@router.get("/knowledge-bases/{kb_id}", summary="获取知识库详情")
async def proxy_get_knowledge_base(kb_id: str, request: Request):
    """代理：获取知识库详情

    Args:
        kb_id: 知识库 ID
        request: FastAPI 请求对象

    Returns:
        IDA 知识库详情响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "GET", f"/knowledge-bases/{kb_id}", token,
    )


@router.put("/knowledge-bases/{kb_id}", summary="更新知识库")
async def proxy_update_knowledge_base(
    kb_id: str,
    request: Request,
    body: UpdateKnowledgeBaseRequest,
):
    """代理：更新知识库

    使用 UpdateKnowledgeBaseRequest 校验请求体，
    所有字段可选，仅更新提交的字段，排除 None 值。

    Args:
        kb_id: 知识库 ID
        request: FastAPI 请求对象
        body: Pydantic 校验后的更新知识库请求体

    Returns:
        IDA 更新知识库响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "PUT", f"/knowledge-bases/{kb_id}", token,
        json=body.model_dump(exclude_none=True),
    )


@router.delete("/knowledge-bases/{kb_id}", summary="删除知识库")
async def proxy_delete_knowledge_base(kb_id: str, request: Request):
    """代理：删除知识库

    Args:
        kb_id: 知识库 ID
        request: FastAPI 请求对象

    Returns:
        IDA 删除知识库响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "DELETE", f"/knowledge-bases/{kb_id}", token,
    )


@router.get("/knowledge-bases/{kb_id}/documents", summary="列出知识库文档")
async def proxy_list_documents(kb_id: str, request: Request, page: int = 1, per_page: int = 20):
    """代理：获取文档列表

    Args:
        kb_id: 知识库 ID
        request: FastAPI 请求对象
        page: 页码
        per_page: 每页数量

    Returns:
        IDA 文档列表响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "GET", f"/knowledge-bases/{kb_id}/documents", token,
        params={"page": page, "per_page": per_page},
    )


@router.post("/knowledge-bases/{kb_id}/documents", summary="上传知识库文档")
async def proxy_upload_document(
    kb_id: str,
    request: Request,
    file: UploadFile = File(...),
    folder_path: str = Form(""),
):
    """代理：上传文档

    将前端上传的文件转发到 IDA，使用独立的超长超时客户端
    以适应大文件上传场景。

    Args:
        kb_id: 知识库 ID
        request: FastAPI 请求对象
        file: 上传的文件对象
        folder_path: 文件夹路径，默认为根目录

    Returns:
        IDA 上传文档响应
    """
    token = await _get_user_token(request)
    file_content = await file.read()
    prefix = get_ida_api_prefix()
    ida_url = f"{settings.ida_backend_url}{prefix}/knowledge-bases/{kb_id}/documents"
    req_headers = {"Authorization": f"Bearer {token}"}
    req_files = {"file": (file.filename, file_content, file.content_type)}
    req_data = {"folder_path": folder_path}

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(ida_url, headers=req_headers, files=req_files, data=req_data)
        _ida_last_connect_ok = True
        return _handle_ida_response(response)
    except AppException:
        raise
    except httpx.ConnectError:
        if not _ida_last_connect_ok:
            try:
                new_prefix = await detect_ida_api_prefix()
                new_url = f"{settings.ida_backend_url}{new_prefix}/knowledge-bases/{kb_id}/documents"
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(new_url, headers=req_headers, files=req_files, data=req_data)
                _ida_last_connect_ok = True
                return _handle_ida_response(response)
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except AppException:
                raise
            except Exception:
                pass
        _ida_last_connect_ok = False
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务连接失败，请确认服务是否正常运行")
    except httpx.TimeoutException:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务响应超时，请稍后重试")
    except Exception as e:
        logger.error("上传文档异常: %s", str(e))
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务异常，请稍后重试")


@router.delete("/knowledge-bases/{kb_id}/documents/{doc_id}", summary="删除知识库文档")
async def proxy_delete_document(kb_id: str, doc_id: str, request: Request):
    """代理：删除文档

    Args:
        kb_id: 知识库 ID
        doc_id: 文档 ID
        request: FastAPI 请求对象

    Returns:
        IDA 删除文档响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "DELETE", f"/knowledge-bases/{kb_id}/documents/{doc_id}", token,
    )


@router.post("/qa/ask", summary="知识库问答")
async def proxy_qa_ask(
    request: Request,
    body: QAAskRequest,
):
    """代理：智能问答

    使用 QAAskRequest 校验请求体，确保 query 非空且长度合法，
    top_k 和 threshold 在合理范围内。

    Args:
        request: FastAPI 请求对象
        body: Pydantic 校验后的问答请求体

    Returns:
        IDA 问答响应
    """
    token = await _get_user_token(request)
    return await _ida_request(
        "POST", "/qa/ask", token,
        json=body.model_dump(exclude_none=True),
        timeout=60.0,
    )


@router.post("/qa/ask/stream", summary="知识库流式问答")
async def proxy_qa_ask_stream(
    request: Request,
    body: QAAskRequest,
):
    """代理：流式问答（SSE 透传）

    将 IDA 的 SSE 流式响应透传到前端，保持事件流格式不变。
    使用独立长超时客户端，适应流式响应的长时间连接特性。

    错误事件格式与前端 chatStreamFetch 解析逻辑对齐：
    - event: error 标识错误类型
    - data 为 JSON 对象，包含 event/error/message 字段
    - 前端通过 JSON.parse(data) 解析后判断 event === 'error' 处理

    Args:
        request: FastAPI 请求对象
        body: Pydantic 校验后的问答请求体

    Returns:
        StreamingResponse: SSE 事件流
    """
    token = await _get_user_token(request)
    prefix = get_ida_api_prefix()
    ida_url = f"{settings.ida_backend_url}{prefix}/qa/ask/stream"
    req_headers = _get_ida_headers(token)
    req_json = body.model_dump(exclude_none=True)

    async def stream_generator():
        """SSE 流生成器

        使用 httpx 流式请求逐块读取 IDA 响应，
        透传每个 SSE 事件到前端。
        错误时发送与前端解析逻辑对齐的 error 事件。
        连接失败时自动重新探测 IDA 并重试一次。
        """
        global _ida_last_connect_ok

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST", ida_url, json=req_json, headers=req_headers,
                ) as response:
                    if response.status_code >= 400:
                        error_text = await response.aread()
                        error_msg = error_text.decode(errors="replace")
                        logger.warning(
                            "IDA 流式问答失败: status=%d, error=%s",
                            response.status_code,
                            error_msg[:200],
                        )
                        try:
                            error_json = json.loads(error_msg)
                            detail = error_json.get("detail", error_json.get("message", error_msg))
                        except (json.JSONDecodeError, TypeError):
                            detail = f"IDA 服务返回 HTTP {response.status_code}"

                        error_code = "IDA_AUTH_ERROR" if response.status_code in (401, 403) else "IDA_SERVICE_ERROR"
                        yield _build_sse_error_event(detail, error_code)
                        return

                    _ida_last_connect_ok = True
                    async for chunk in response.aiter_text():
                        yield chunk
        except httpx.ConnectError as e:
            if not _ida_last_connect_ok:
                try:
                    new_prefix = await detect_ida_api_prefix()
                    new_url = f"{settings.ida_backend_url}{new_prefix}/qa/ask/stream"
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        async with client.stream(
                            "POST", new_url, json=req_json, headers=req_headers,
                        ) as response:
                            if response.status_code >= 400:
                                error_text = await response.aread()
                                error_msg = error_text.decode(errors="replace")
                                try:
                                    error_json = json.loads(error_msg)
                                    detail = error_json.get("detail", error_json.get("message", error_msg))
                                except (json.JSONDecodeError, TypeError):
                                    detail = f"IDA 服务返回 HTTP {response.status_code}"
                                error_code = "IDA_AUTH_ERROR" if response.status_code in (401, 403) else "IDA_SERVICE_ERROR"
                                yield _build_sse_error_event(detail, error_code)
                                return

                            _ida_last_connect_ok = True
                            async for chunk in response.aiter_text():
                                yield chunk
                    return
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                except Exception:
                    pass
            _ida_last_connect_ok = False
            logger.error("IDA 流式问答连接失败: %s", str(e))
            yield _build_sse_error_event("知识库服务连接失败，请稍后重试", "IDA_SERVICE_ERROR")
        except httpx.ReadTimeout as e:
            logger.error("IDA 流式问答读取超时: %s", str(e))
            yield _build_sse_error_event("知识库服务响应超时，请稍后重试", "IDA_SERVICE_ERROR")
        except Exception as e:
            logger.error("IDA 流式问答异常: %s", str(e), exc_info=True)
            yield _build_sse_error_event("知识库服务异常，请稍后重试", "IDA_SERVICE_ERROR")

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/qa/parse-files", summary="解析文件")
async def proxy_parse_files(request: Request):
    """代理：文件解析

    将前端上传的多个文件转发到 IDA 进行内容解析。
    使用独立超长超时客户端，适应多文件解析场景。

    Args:
        request: FastAPI 请求对象，包含 files 表单字段

    Returns:
        IDA 文件解析响应，包含每个文件的解析结果
    """
    token = await _get_user_token(request)
    form = await request.form()
    files = form.getlist("files")

    file_list = []
    for f in files:
        content = await f.read()
        file_list.append(("files", (f.filename, content, f.content_type)))

    prefix = get_ida_api_prefix()
    ida_url = f"{settings.ida_backend_url}{prefix}/qa/parse-files"
    req_headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(ida_url, headers=req_headers, files=file_list)
        _ida_last_connect_ok = True
        return _handle_ida_response(response)
    except AppException:
        raise
    except httpx.ConnectError:
        if not _ida_last_connect_ok:
            try:
                new_prefix = await detect_ida_api_prefix()
                new_url = f"{settings.ida_backend_url}{new_prefix}/qa/parse-files"
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(new_url, headers=req_headers, files=file_list)
                _ida_last_connect_ok = True
                return _handle_ida_response(response)
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except AppException:
                raise
            except Exception:
                pass
        _ida_last_connect_ok = False
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务连接失败，请确认服务是否正常运行")
    except httpx.TimeoutException:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务响应超时，请稍后重试")
    except Exception as e:
        logger.error("文件解析异常: %s", str(e))
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务异常，请稍后重试")


@router.post("/qa/analyze-image", summary="分析图片")
async def proxy_analyze_image(
    request: Request,
    image: UploadFile = File(...),
    query: str = Form("请描述这张图片的内容"),
):
    """代理：图片分析

    将前端上传的图片转发到 IDA 进行内容分析。

    Args:
        request: FastAPI 请求对象
        image: 上传的图片文件
        query: 分析提示语，默认为"请描述这张图片的内容"

    Returns:
        IDA 图片分析响应
    """
    token = await _get_user_token(request)
    image_content = await image.read()

    prefix = get_ida_api_prefix()
    ida_url = f"{settings.ida_backend_url}{prefix}/qa/analyze-image"
    req_headers = {"Authorization": f"Bearer {token}"}
    req_files = {"image": (image.filename, image_content, image.content_type)}
    req_data = {"query": query}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(ida_url, headers=req_headers, files=req_files, data=req_data)
        _ida_last_connect_ok = True
        return _handle_ida_response(response)
    except AppException:
        raise
    except httpx.ConnectError:
        if not _ida_last_connect_ok:
            try:
                new_prefix = await detect_ida_api_prefix()
                new_url = f"{settings.ida_backend_url}{new_prefix}/qa/analyze-image"
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(new_url, headers=req_headers, files=req_files, data=req_data)
                _ida_last_connect_ok = True
                return _handle_ida_response(response)
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            except AppException:
                raise
            except Exception:
                pass
        _ida_last_connect_ok = False
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务连接失败，请确认服务是否正常运行")
    except httpx.TimeoutException:
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务响应超时，请稍后重试")
    except Exception as e:
        logger.error("图片分析异常: %s", str(e))
        raise AppException(ErrorCode.SERVICE_UNAVAILABLE, message="知识库服务异常，请稍后重试")

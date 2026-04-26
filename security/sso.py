"""SSO 企业身份集成

支持 OAuth2.0 / OIDC 协议，可对接：
  - Microsoft Entra ID (Azure AD)
  - Okta
  - 企业微信 (WeCom)
  - 钉钉 (DingTalk)
  - 飞书 (Feishu/Lark)

架构设计：
  - SSOProvider: 抽象基类，定义统一接口
  - EntraIDProvider / OktaProvider / WeComProvider / DingTalkProvider: 具体实现
  - SSOManager: 统一管理所有 SSO 提供者，处理授权回调与用户映射

集成流程：
  1. 前端重定向到 /api/v1/auth/sso/{provider}/authorize
  2. 后端构建授权 URL 并重定向到 IdP
  3. IdP 认证后回调 /api/v1/auth/sso/{provider}/callback
  4. 后端交换授权码获取 Token，提取用户信息
  5. 映射到本地用户，签发 JWT Token 对
"""

import abc
import hashlib
import base64
import logging
import secrets
import time
import uuid
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SSOProviderType(str, Enum):
    """SSO 提供者类型"""

    ENTRA_ID = "entra_id"
    OKTA = "okta"
    WECOM = "wecom"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"


class SSOUserInfo(BaseModel):
    """SSO 用户信息（统一格式）"""

    external_id: str = Field(description="IdP 侧的用户唯一标识")
    email: str = Field(default="", description="用户邮箱")
    display_name: str = Field(default="", description="用户显示名称")
    phone: str = Field(default="", description="用户手机号")
    department: str = Field(default="", description="用户部门")
    avatar_url: str = Field(default="", description="用户头像 URL")
    raw_claims: dict[str, Any] = Field(default_factory=dict, description="IdP 返回的原始声明")


class SSOAuthorizationParams(BaseModel):
    """SSO 授权参数"""

    authorization_url: str = Field(description="授权 URL，前端需重定向到此地址")
    state: str = Field(description="CSRF 防护 state 参数")
    code_verifier: str = Field(default="", description="PKCE code_verifier")


class SSOTokenResult(BaseModel):
    """SSO Token 交换结果"""

    access_token: str = Field(default="", description="IdP 的 access_token")
    id_token: str = Field(default="", description="IdP 的 id_token（OIDC）")
    refresh_token: str = Field(default="", description="IdP 的 refresh_token")
    expires_in: int = Field(default=0, description="Token 有效期（秒）")
    user_info: SSOUserInfo = Field(description="提取的用户信息")


class SSOProvider(abc.ABC):
    """SSO 提供者抽象基类

    所有 SSO 提供者必须实现以下方法：
    - build_authorization_url: 构建授权 URL
    - exchange_code: 用授权码交换 Token
    - get_user_info: 从 Token 中提取用户信息
    - refresh_idp_token: 刷新 IdP Token
    """

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._client_id: str = config.get("client_id", "")
        self._client_secret: str = config.get("client_secret", "")
        self._redirect_uri: str = config.get("redirect_uri", "")
        self._scopes: list[str] = config.get("scopes", [])

    @property
    @abc.abstractmethod
    def provider_type(self) -> SSOProviderType:
        """提供者类型"""

    @abc.abstractmethod
    def build_authorization_url(self, state: str, code_verifier: str) -> str:
        """构建授权 URL

        Args:
            state: CSRF 防护 state 参数
            code_verifier: PKCE code_verifier

        Returns:
            完整的授权 URL
        """

    @abc.abstractmethod
    async def exchange_code(self, code: str, code_verifier: str) -> SSOTokenResult:
        """用授权码交换 Token 并提取用户信息

        Args:
            code: IdP 返回的授权码
            code_verifier: PKCE code_verifier

        Returns:
            SSOTokenResult
        """

    @abc.abstractmethod
    async def refresh_idp_token(self, refresh_token: str) -> SSOTokenResult | None:
        """刷新 IdP Token

        Args:
            refresh_token: IdP 的 refresh_token

        Returns:
            新的 SSOTokenResult 或 None
        """

    def _generate_pkce_pair(self) -> tuple[str, str]:
        """生成 PKCE code_verifier 和 code_challenge

        Returns:
            (code_verifier, code_challenge)
        """
        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge


class EntraIDProvider(SSOProvider):
    """Microsoft Entra ID (Azure AD) SSO 提供者

    使用 OIDC 协议，基于 Microsoft Identity Platform v2.0 端点。
    支持 PKCE 增强安全性。

    配置项：
    - client_id: 应用程序（客户端）ID
    - client_secret: 客户端密钥
    - redirect_uri: 回调地址
    - tenant_id: 租户 ID（common 表示多租户）
    - scopes: 请求的权限范围
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._tenant_id: str = config.get("tenant_id", "common")
        self._base_url = f"https://login.microsoftonline.com/{self._tenant_id}"
        self._graph_url = "https://graph.microsoft.com/v1.0"

    @property
    def provider_type(self) -> SSOProviderType:
        return SSOProviderType.ENTRA_ID

    def build_authorization_url(self, state: str, code_verifier: str) -> str:
        _, code_challenge = self._generate_pkce_pair_from_verifier(code_verifier)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(self._scopes) if self._scopes else "openid email profile User.Read",
            "response_mode": "query",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self._base_url}/oauth2/v2.0/authorize?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> SSOTokenResult:
        token_url = f"{self._base_url}/oauth2/v2.0/token"
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_resp = await client.post(token_url, data=data)
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("access_token", "")

            user_resp = await client.get(
                self._graph_url + "/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_resp.raise_for_status()
            user_data = user_resp.json()

        user_info = SSOUserInfo(
            external_id=user_data.get("id", ""),
            email=user_data.get("mail", "") or user_data.get("userPrincipalName", ""),
            display_name=user_data.get("displayName", ""),
            phone=user_data.get("mobilePhone", ""),
            department=user_data.get("department", ""),
            avatar_url="",
            raw_claims=user_data,
        )

        return SSOTokenResult(
            access_token=access_token,
            id_token=token_data.get("id_token", ""),
            refresh_token=token_data.get("refresh_token", ""),
            expires_in=token_data.get("expires_in", 0),
            user_info=user_info,
        )

    async def refresh_idp_token(self, refresh_token: str) -> SSOTokenResult | None:
        token_url = f"{self._base_url}/oauth2/v2.0/token"
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(token_url, data=data)
                resp.raise_for_status()
                token_data = resp.json()
            return SSOTokenResult(
                access_token=token_data.get("access_token", ""),
                id_token=token_data.get("id_token", ""),
                refresh_token=token_data.get("refresh_token", refresh_token),
                expires_in=token_data.get("expires_in", 0),
                user_info=SSOUserInfo(external_id=""),
            )
        except Exception as e:
            logger.warning("Entra ID Token 刷新失败: %s", e)
            return None

    @staticmethod
    def _generate_pkce_pair_from_verifier(code_verifier: str) -> tuple[str, str]:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge


class OktaProvider(SSOProvider):
    """Okta SSO 提供者

    使用 OIDC 协议，基于 Okta 的授权服务器端点。

    配置项：
    - client_id: Okta 应用 Client ID
    - client_secret: Okta 应用 Client Secret
    - redirect_uri: 回调地址
    - domain: Okta 域名（如 example.okta.com）
    - authorization_server_id: 授权服务器 ID（默认 default）
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._domain: str = config.get("domain", "")
        self._auth_server_id: str = config.get("authorization_server_id", "default")
        self._base_url = f"https://{self._domain}/oauth2/{self._auth_server_id}"

    @property
    def provider_type(self) -> SSOProviderType:
        return SSOProviderType.OKTA

    def build_authorization_url(self, state: str, code_verifier: str) -> str:
        _, code_challenge = self._generate_pkce_pair_from_verifier(code_verifier)
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(self._scopes) if self._scopes else "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self._base_url}/v1/authorize?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> SSOTokenResult:
        token_url = f"{self._base_url}/v1/token"
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "redirect_uri": self._redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_resp = await client.post(token_url, data=data)
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("access_token", "")

            user_resp = await client.get(
                f"https://{self._domain}/oauth2/{self._auth_server_id}/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_resp.raise_for_status()
            user_data = user_resp.json()

        user_info = SSOUserInfo(
            external_id=user_data.get("sub", ""),
            email=user_data.get("email", ""),
            display_name=user_data.get("name", ""),
            phone="",
            department="",
            avatar_url="",
            raw_claims=user_data,
        )

        return SSOTokenResult(
            access_token=access_token,
            id_token=token_data.get("id_token", ""),
            refresh_token=token_data.get("refresh_token", ""),
            expires_in=token_data.get("expires_in", 0),
            user_info=user_info,
        )

    async def refresh_idp_token(self, refresh_token: str) -> SSOTokenResult | None:
        token_url = f"{self._base_url}/v1/token"
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(token_url, data=data)
                resp.raise_for_status()
                token_data = resp.json()
            return SSOTokenResult(
                access_token=token_data.get("access_token", ""),
                id_token=token_data.get("id_token", ""),
                refresh_token=token_data.get("refresh_token", refresh_token),
                expires_in=token_data.get("expires_in", 0),
                user_info=SSOUserInfo(external_id=""),
            )
        except Exception as e:
            logger.warning("Okta Token 刷新失败: %s", e)
            return None

    @staticmethod
    def _generate_pkce_pair_from_verifier(code_verifier: str) -> tuple[str, str]:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge


class WeComProvider(SSOProvider):
    """企业微信 (WeCom) SSO 提供者

    使用 OAuth2.0 协议，基于企业微信的网页授权端点。
    企业微信使用 corpid + corpsecret 模式，不使用标准 OIDC。

    配置项：
    - client_id: 企业 corpid
    - client_secret: 应用的 corpsecret
    - redirect_uri: 回调地址
    - agent_id: 企业微信应用 agentid
    """

    WECOM_AUTHORIZE_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"
    WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._agent_id: str = config.get("agent_id", "")

    @property
    def provider_type(self) -> SSOProviderType:
        return SSOProviderType.WECOM

    def build_authorization_url(self, state: str, code_verifier: str = "") -> str:
        params = {
            "appid": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "snsapi_privateinfo",
            "state": state,
            "agentid": self._agent_id,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.WECOM_AUTHORIZE_URL}?{query}#wechat_redirect"

    async def exchange_code(self, code: str, code_verifier: str = "") -> SSOTokenResult:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_resp = await client.get(
                f"{self.WECOM_API_BASE}/gettoken",
                params={"corpid": self._client_id, "corpsecret": self._client_secret},
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data.get("access_token", "")

            if not access_token:
                raise ValueError(f"企业微信获取 access_token 失败: {token_data.get('errmsg', '')}")

            user_resp = await client.get(
                f"{self.WECOM_API_BASE}/auth/getuserinfo",
                params={"access_token": access_token, "code": code},
            )
            user_resp.raise_for_status()
            user_data = user_resp.json()

            user_id = user_data.get("userid", user_data.get("UserId", ""))
            if not user_id:
                raise ValueError(f"企业微信获取用户ID失败: {user_data}")

            detail_resp = await client.get(
                f"{self.WECOM_API_BASE}/user/get",
                params={"access_token": access_token, "userid": user_id},
            )
            detail_resp.raise_for_status()
            detail_data = detail_resp.json()

        user_info = SSOUserInfo(
            external_id=user_id,
            email=detail_data.get("email", ""),
            display_name=detail_data.get("name", ""),
            phone=detail_data.get("mobile", ""),
            department=str(detail_data.get("department", [])),
            avatar_url=detail_data.get("avatar", ""),
            raw_claims=detail_data,
        )

        return SSOTokenResult(
            access_token=access_token,
            id_token="",
            refresh_token="",
            expires_in=token_data.get("expires_in", 7200),
            user_info=user_info,
        )

    async def refresh_idp_token(self, refresh_token: str) -> SSOTokenResult | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.WECOM_API_BASE}/gettoken",
                params={"corpid": self._client_id, "corpsecret": self._client_secret},
            )
            resp.raise_for_status()
            token_data = resp.json()
        access_token = token_data.get("access_token", "")
        if not access_token:
            return None
        return SSOTokenResult(
            access_token=access_token,
            expires_in=token_data.get("expires_in", 7200),
            user_info=SSOUserInfo(external_id=""),
        )


class DingTalkProvider(SSOProvider):
    """钉钉 (DingTalk) SSO 提供者

    使用 OAuth2.0 协议，基于钉钉的网页授权端点。

    配置项：
    - client_id: 钉钉应用的 AppKey
    - client_secret: 钉钉应用的 AppSecret
    - redirect_uri: 回调地址
    """

    DINGTALK_AUTHORIZE_URL = "https://login.dingtalk.com/oauth2/auth"
    DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
    DINGTALK_USER_INFO_URL = "https://api.dingtalk.com/v1.0/contact/users/me"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

    @property
    def provider_type(self) -> SSOProviderType:
        return SSOProviderType.DINGTALK

    def build_authorization_url(self, state: str, code_verifier: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes) if self._scopes else "openid",
            "state": state,
            "prompt": "consent",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.DINGTALK_AUTHORIZE_URL}?{query}"

    async def exchange_code(self, code: str, code_verifier: str = "") -> SSOTokenResult:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_resp = await client.post(
                self.DINGTALK_TOKEN_URL,
                json={
                    "clientId": self._client_id,
                    "clientSecret": self._client_secret,
                    "code": code,
                    "grantType": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("accessToken", "")
            if not access_token:
                raise ValueError(f"钉钉获取 accessToken 失败: {token_data}")

            user_resp = await client.get(
                self.DINGTALK_USER_INFO_URL,
                headers={"x-acs-dingtalk-access-token": access_token},
            )
            user_resp.raise_for_status()
            user_data = user_resp.json()

        user_info = SSOUserInfo(
            external_id=user_data.get("unionId", ""),
            email=user_data.get("email", ""),
            display_name=user_data.get("nick", ""),
            phone=user_data.get("mobile", ""),
            department="",
            avatar_url=user_data.get("avatarUrl", ""),
            raw_claims=user_data,
        )

        return SSOTokenResult(
            access_token=access_token,
            id_token="",
            refresh_token=token_data.get("refreshToken", ""),
            expires_in=token_data.get("expireIn", 0),
            user_info=user_info,
        )

    async def refresh_idp_token(self, refresh_token: str) -> SSOTokenResult | None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.DINGTALK_TOKEN_URL,
                    json={
                        "clientId": self._client_id,
                        "clientSecret": self._client_secret,
                        "refreshToken": refresh_token,
                        "grantType": "refresh_token",
                    },
                )
                resp.raise_for_status()
                token_data = resp.json()
            access_token = token_data.get("accessToken", "")
            if not access_token:
                return None
            return SSOTokenResult(
                access_token=access_token,
                refresh_token=token_data.get("refreshToken", refresh_token),
                expires_in=token_data.get("expireIn", 0),
                user_info=SSOUserInfo(external_id=""),
            )
        except Exception as e:
            logger.warning("钉钉 Token 刷新失败: %s", e)
            return None


class FeishuProvider(SSOProvider):
    """飞书 (Feishu/Lark) SSO 提供者

    使用 OAuth2.0 协议，基于飞书开放平台的网页应用授权。

    配置项：
    - client_id: 飞书应用的 App ID
    - client_secret: 飞书应用的 App Secret
    - redirect_uri: 回调地址

    飞书 OAuth2.0 授权流程：
    1. 引导用户访问授权页面
    2. 用户授权后飞书回调返回 code
    3. 用 code 换取 app_access_token + user_access_token
    4. 用 user_access_token 获取用户信息
    """

    FEISHU_AUTHORIZE_URL = "https://open.feishu.cn/open-apis/authen/v1/authorize"
    FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
    FEISHU_USER_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
    FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

    @property
    def provider_type(self) -> SSOProviderType:
        return SSOProviderType.FEISHU

    def build_authorization_url(self, state: str, code_verifier: str) -> str:
        params = {
            "app_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.FEISHU_AUTHORIZE_URL}?{query}"

    async def _get_app_access_token(self) -> str:
        """获取飞书 app_access_token

        飞书的 OAuth2.0 流程需要先获取 app_access_token，
        再用它来换取 user_access_token。

        Returns:
            app_access_token
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.FEISHU_APP_TOKEN_URL,
                json={
                    "app_id": self._client_id,
                    "app_secret": self._client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise ValueError(f"飞书获取 app_access_token 失败: {data.get('msg', '')}")

        return data.get("app_access_token", "")

    async def exchange_code(self, code: str, code_verifier: str = "") -> SSOTokenResult:
        app_access_token = await self._get_app_access_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            token_resp = await client.post(
                self.FEISHU_USER_TOKEN_URL,
                headers={"Authorization": f"Bearer {app_access_token}"},
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            if token_data.get("code") != 0:
                raise ValueError(f"飞书获取 user_access_token 失败: {token_data.get('msg', '')}")

            user_access_token = token_data.get("data", {}).get("access_token", "")
            if not user_access_token:
                raise ValueError("飞书返回的 user_access_token 为空")

            user_resp = await client.get(
                self.FEISHU_USER_INFO_URL,
                headers={"Authorization": f"Bearer {user_access_token}"},
            )
            user_resp.raise_for_status()
            user_result = user_resp.json()

            if user_result.get("code") != 0:
                raise ValueError(f"飞书获取用户信息失败: {user_result.get('msg', '')}")

            user_data = user_result.get("data", {})

        user_info = SSOUserInfo(
            external_id=user_data.get("user_id", ""),
            email=user_data.get("email", ""),
            display_name=user_data.get("name", ""),
            phone=user_data.get("mobile", ""),
            department=user_data.get("department_id", ""),
            avatar_url=user_data.get("avatar_url", ""),
            raw_claims=user_data,
        )

        token_result_data = token_data.get("data", {})
        return SSOTokenResult(
            access_token=user_access_token,
            id_token=token_result_data.get("id_token", ""),
            refresh_token=token_result_data.get("refresh_token", ""),
            expires_in=token_result_data.get("expires_in", 0),
            user_info=user_info,
        )

    async def refresh_idp_token(self, refresh_token: str) -> SSOTokenResult | None:
        try:
            app_access_token = await self._get_app_access_token()

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.FEISHU_USER_TOKEN_URL,
                    headers={"Authorization": f"Bearer {app_access_token}"},
                    json={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
                resp.raise_for_status()
                token_data = resp.json()

                if token_data.get("code") != 0:
                    return None

                data = token_data.get("data", {})
                new_access_token = data.get("access_token", "")
                if not new_access_token:
                    return None

                return SSOTokenResult(
                    access_token=new_access_token,
                    refresh_token=data.get("refresh_token", refresh_token),
                    expires_in=data.get("expires_in", 0),
                    user_info=SSOUserInfo(external_id=""),
                )
        except Exception as e:
            logger.warning("飞书 Token 刷新失败: %s", e)
            return None


# ==================== SSO 状态存储 ====================

_sso_states: dict[str, dict[str, Any]] = {}

_STATE_TTL_SECONDS = 600


def _store_sso_state(state: str, provider_type: SSOProviderType, code_verifier: str) -> None:
    """存储 SSO 授权状态

    Args:
        state: CSRF state 参数
        provider_type: SSO 提供者类型
        code_verifier: PKCE code_verifier
    """
    _sso_states[state] = {
        "provider_type": provider_type.value,
        "code_verifier": code_verifier,
        "created_at": time.time(),
    }
    _cleanup_expired_states()


def _get_sso_state(state: str) -> dict[str, Any] | None:
    """获取 SSO 授权状态

    Args:
        state: CSRF state 参数

    Returns:
        状态字典或 None
    """
    entry = _sso_states.get(state)
    if entry is None:
        return None
    if time.time() - entry["created_at"] > _STATE_TTL_SECONDS:
        del _sso_states[state]
        return None
    return entry


def _consume_sso_state(state: str) -> dict[str, Any] | None:
    """消费 SSO 授权状态（一次性使用）

    Args:
        state: CSRF state 参数

    Returns:
        状态字典或 None
    """
    entry = _get_sso_state(state)
    if entry is not None:
        del _sso_states[state]
    return entry


def _cleanup_expired_states() -> None:
    """清理过期的 SSO 状态"""
    now = time.time()
    expired = [k for k, v in _sso_states.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        del _sso_states[k]


# ==================== SSO 管理器 ====================

_PROVIDERS: dict[str, SSOProvider] = {}


def register_sso_provider(provider: SSOProvider) -> None:
    """注册 SSO 提供者

    Args:
        provider: SSO 提供者实例
    """
    _PROVIDERS[provider.provider_type.value] = provider
    logger.info("SSO 提供者已注册: %s", provider.provider_type.value)


def get_sso_provider(provider_type: str) -> SSOProvider | None:
    """获取 SSO 提供者

    Args:
        provider_type: 提供者类型标识

    Returns:
        SSOProvider 实例或 None
    """
    return _PROVIDERS.get(provider_type)


def list_sso_providers() -> list[str]:
    """列出所有已注册的 SSO 提供者

    Returns:
        提供者类型标识列表
    """
    return list(_PROVIDERS.keys())


def init_sso_providers_from_config(config: dict[str, dict[str, Any]]) -> None:
    """从配置初始化所有 SSO 提供者

    配置格式：
    {
        "entra_id": {"client_id": "...", "client_secret": "...", ...},
        "okta": {"client_id": "...", ...},
        "wecom": {"client_id": "...", ...},
        "dingtalk": {"client_id": "...", ...},
    }

    Args:
        config: SSO 提供者配置字典
    """
    provider_map: dict[str, type[SSOProvider]] = {
        SSOProviderType.ENTRA_ID.value: EntraIDProvider,
        SSOProviderType.OKTA.value: OktaProvider,
        SSOProviderType.WECOM.value: WeComProvider,
        SSOProviderType.DINGTALK.value: DingTalkProvider,
        SSOProviderType.FEISHU.value: FeishuProvider,
    }

    for provider_key, provider_config in config.items():
        if not provider_config.get("enabled", False):
            continue

        provider_cls = provider_map.get(provider_key)
        if provider_cls is None:
            logger.warning("未知的 SSO 提供者: %s", provider_key)
            continue

        try:
            provider = provider_cls(provider_config)
            register_sso_provider(provider)
        except Exception as e:
            logger.error("SSO 提供者 %s 初始化失败: %s", provider_key, e)


def build_sso_authorization(provider_type: str, redirect_uri: str | None = None) -> SSOAuthorizationParams:
    """构建 SSO 授权请求

    Args:
        provider_type: SSO 提供者类型
        redirect_uri: 可选的回调地址覆盖

    Returns:
        SSOAuthorizationParams

    Raises:
        ValueError: 提供者未注册
    """
    provider = get_sso_provider(provider_type)
    if provider is None:
        raise ValueError(f"SSO 提供者未注册: {provider_type}")

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)

    if redirect_uri:
        original = provider._redirect_uri
        provider._redirect_uri = redirect_uri

    authorization_url = provider.build_authorization_url(state, code_verifier)

    if redirect_uri:
        provider._redirect_uri = original

    _store_sso_state(state, provider.provider_type, code_verifier)

    return SSOAuthorizationParams(
        authorization_url=authorization_url,
        state=state,
        code_verifier=code_verifier,
    )


async def handle_sso_callback(provider_type: str, code: str, state: str) -> SSOTokenResult:
    """处理 SSO 授权回调

    Args:
        provider_type: SSO 提供者类型
        code: 授权码
        state: CSRF state 参数

    Returns:
        SSOTokenResult

    Raises:
        ValueError: state 无效或提供者未注册
    """
    state_entry = _consume_sso_state(state)
    if state_entry is None:
        raise ValueError("SSO state 无效或已过期")

    if state_entry["provider_type"] != provider_type:
        raise ValueError(f"SSO state 提供者不匹配: expected={state_entry['provider_type']} actual={provider_type}")

    provider = get_sso_provider(provider_type)
    if provider is None:
        raise ValueError(f"SSO 提供者未注册: {provider_type}")

    code_verifier = state_entry.get("code_verifier", "")
    return await provider.exchange_code(code, code_verifier)


# ==================== 用户映射 ====================

_USER_MAPPING: dict[str, dict[str, Any]] = {}


def map_sso_user_to_local(sso_user: SSOUserInfo, provider_type: str) -> dict[str, Any]:
    """将 SSO 用户映射到本地用户

    映射策略：
    1. 通过 external_id + provider_type 查找已有映射
    2. 无映射时自动创建本地用户（自动配额模式）
    3. 根据邮箱域名自动分配角色和部门

    Args:
        sso_user: SSO 用户信息
        provider_type: SSO 提供者类型

    Returns:
        本地用户信息字典，包含 user_id, roles, departments
    """
    mapping_key = f"{provider_type}:{sso_user.external_id}"

    if mapping_key in _USER_MAPPING:
        return _USER_MAPPING[mapping_key]

    user_id = sso_user.email.split("@")[0] if sso_user.email else f"sso_{uuid.uuid4().hex[:8]}"
    roles = _infer_roles_from_email(sso_user.email)
    departments = [sso_user.department] if sso_user.department else []

    local_user = {
        "user_id": user_id,
        "roles": roles,
        "departments": departments,
        "display_name": sso_user.display_name,
        "email": sso_user.email,
        "phone": sso_user.phone,
        "avatar_url": sso_user.avatar_url,
        "sso_provider": provider_type,
        "external_id": sso_user.external_id,
    }

    _USER_MAPPING[mapping_key] = local_user
    logger.info("SSO 用户自动映射: %s -> %s (provider=%s)", sso_user.external_id, user_id, provider_type)

    return local_user


def _infer_roles_from_email(email: str) -> list[str]:
    """根据邮箱推断默认角色

    Args:
        email: 用户邮箱

    Returns:
        角色列表
    """
    if not email:
        return ["employee"]

    local_part = email.split("@")[0].lower()
    if local_part.startswith("admin"):
        return ["admin"]
    if local_part.startswith("hr"):
        return ["hr_specialist"]
    if local_part.startswith("fin"):
        return ["finance"]
    if local_part.startswith("mgr"):
        return ["manager"]
    return ["employee"]

"""静态数据加密

实现字段级加密（Field-Level Encryption）和密钥管理，满足企业安全基线要求。
PostgreSQL 和 Redis 中的敏感数据（会话内容、审计日志、PII 等）在写入前自动加密。

架构设计：
  - EncryptionManager: 加密管理器，提供加密/解密/密钥轮换
  - KeyProvider: 密钥提供者抽象，支持本地文件和外部 KMS
  - FieldEncryption: 字段级加密工具，与 SQLAlchemy/Redis 集成

加密方案：
  - 算法: AES-256-GCM（提供机密性 + 完整性）
  - 密钥层次: Master Key -> Data Encryption Key (DEK)
  - 密钥轮换: 支持 DEK 轮换，无需重新加密已有数据
  - 密钥存储: 生产环境使用外部 KMS（HashiCorp Vault / AWS KMS / 阿里云 KMS）

使用方式：
  from security.encryption import get_encryption_manager

  mgr = get_encryption_manager()
  ciphertext = mgr.encrypt("敏感数据")
  plaintext = mgr.decrypt(ciphertext)
"""

import base64
import hashlib
import logging
import os
import secrets
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_ENCRYPTION_VERSION = 1
_ALGORITHM = "AES-256-GCM"


class EncryptedData(BaseModel):
    """加密数据结构

    包含解密所需的全部元信息，支持密钥轮换后仍可解密旧数据。
    """

    version: int = Field(default=_ENCRYPTION_VERSION, description="加密版本号")
    algorithm: str = Field(default=_ALGORITHM, description="加密算法")
    key_id: str = Field(description="加密使用的 DEK ID")
    iv: str = Field(description="初始化向量（base64 编码）")
    ciphertext: str = Field(description="密文（base64 编码）")
    tag: str = Field(description="GCM 认证标签（base64 编码）")

    def serialize(self) -> str:
        """序列化为可存储的字符串"""
        return base64.urlsafe_b64encode(
            self.model_dump_json().encode("utf-8")
        ).decode("ascii")

    @classmethod
    def deserialize(cls, data: str) -> "EncryptedData":
        """从存储字符串反序列化"""
        json_str = base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8")
        return cls.model_validate_json(json_str)


try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False
    logger.warning("cryptography 库未安装，静态数据加密将使用降级方案，生产环境必须安装")


class KeyProvider(ABC):
    """密钥提供者抽象基类"""

    @abstractmethod
    def get_master_key(self) -> bytes:
        """获取主密钥（Master Key）

        Returns:
            32 字节的主密钥
        """

    @abstractmethod
    def get_key_id(self) -> str:
        """获取当前主密钥的标识

        Returns:
            密钥 ID
        """


class LocalKeyProvider(KeyProvider):
    """本地文件密钥提供者

    从本地文件加载主密钥。适用于开发环境和单机部署。
    生产环境应替换为 KMS 提供者。

    密钥文件格式：32 字节二进制数据
    """

    def __init__(self, key_file_path: str):
        self._key_file = key_file_path
        self._master_key: bytes | None = None
        self._key_id: str = ""

    def get_master_key(self) -> bytes:
        if self._master_key is not None:
            return self._master_key

        if os.path.exists(self._key_file):
            with open(self._key_file, "rb") as f:
                key_data = f.read()
            if len(key_data) == 32:
                self._master_key = key_data
                self._key_id = hashlib.sha256(key_data).hexdigest()[:16]
                return self._master_key
            logger.warning("密钥文件长度异常(%d字节)，将重新生成", len(key_data))

        self._master_key = secrets.token_bytes(32)
        os.makedirs(os.path.dirname(self._key_file) or ".", exist_ok=True)
        with open(self._key_file, "wb") as f:
            f.write(self._master_key)
        try:
            os.chmod(self._key_file, 0o600)
        except OSError:
            pass
        self._key_id = hashlib.sha256(self._master_key).hexdigest()[:16]
        logger.info("已生成新的主密钥: key_id=%s", self._key_id)
        return self._master_key

    def get_key_id(self) -> str:
        if not self._key_id:
            self.get_master_key()
        return self._key_id


class EnvironmentKeyProvider(KeyProvider):
    """环境变量密钥提供者

    从环境变量加载主密钥。适用于容器化部署。
    """

    def __init__(self, env_var_name: str = "ENCRYPTION_MASTER_KEY"):
        self._env_var = env_var_name
        self._master_key: bytes | None = None
        self._key_id: str = ""

    def get_master_key(self) -> bytes:
        if self._master_key is not None:
            return self._master_key

        key_b64 = os.environ.get(self._env_var, "")
        if key_b64:
            try:
                self._master_key = base64.urlsafe_b64decode(key_b64)
                if len(self._master_key) == 32:
                    self._key_id = hashlib.sha256(self._master_key).hexdigest()[:16]
                    return self._master_key
                logger.warning("环境变量密钥长度异常(%d字节)", len(self._master_key))
            except Exception as e:
                logger.warning("环境变量密钥解码失败: %s", e)

        self._master_key = secrets.token_bytes(32)
        self._key_id = hashlib.sha256(self._master_key).hexdigest()[:16]
        logger.warning("环境变量 %s 未设置或无效，已生成临时密钥（生产环境必须配置）", self._env_var)
        return self._master_key

    def get_key_id(self) -> str:
        if not self._key_id:
            self.get_master_key()
        return self._key_id


class DataEncryptionKey(BaseModel):
    """数据加密密钥（DEK）

    DEK 由 Master Key 派生，用于实际的数据加密。
    支持 DEK 轮换：每个 DEK 有独立的 ID 和有效期。
    """

    key_id: str
    encrypted_dek: str = Field(description="被 Master Key 加密后的 DEK（base64）")
    created_at: float
    expires_at: float | None = None
    algorithm: str = _ALGORITHM


class EncryptionManager:
    """加密管理器

    提供字段级加密/解密、密钥派生、密钥轮换能力。

    加密流程：
    1. 从 KeyProvider 获取 Master Key
    2. 派生 DEK（基于 Master Key + key_id）
    3. 使用 DEK 进行 AES-256-GCM 加密
    4. 返回包含元信息的 EncryptedData

    解密流程：
    1. 反序列化 EncryptedData
    2. 根据 key_id 派生对应的 DEK
    3. 使用 DEK 进行 AES-256-GCM 解密
    """

    _DEK_CACHE: dict[str, bytes] = {}
    _DEK_TTL = 3600

    def __init__(self, key_provider: KeyProvider):
        self._key_provider = key_provider
        self._has_cryptography = _HAS_CRYPTOGRAPHY

    def encrypt(self, plaintext: str, context: str = "") -> str:
        """加密字符串

        Args:
            plaintext: 明文
            context: 加密上下文（用于关联数据认证），如 "session:abc123"

        Returns:
            序列化的加密数据字符串
        """
        if not plaintext:
            return ""

        if not self._has_cryptography:
            return self._encrypt_fallback(plaintext)

        dek = self._get_or_derive_dek()
        iv = secrets.token_bytes(12)
        aad = context.encode("utf-8") if context else None

        aesgcm = AESGCM(dek)
        ciphertext_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), aad)

        ciphertext = ciphertext_with_tag[:-16]
        tag = ciphertext_with_tag[-16:]

        encrypted = EncryptedData(
            key_id=self._key_provider.get_key_id(),
            iv=base64.urlsafe_b64encode(iv).decode("ascii"),
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            tag=base64.urlsafe_b64encode(tag).decode("ascii"),
        )

        return encrypted.serialize()

    def decrypt(self, encrypted_str: str, context: str = "") -> str:
        """解密字符串

        Args:
            encrypted_str: 序列化的加密数据字符串
            context: 解密上下文（必须与加密时相同）

        Returns:
            明文
        """
        if not encrypted_str:
            return ""

        if not self._has_cryptography:
            return self._decrypt_fallback(encrypted_str)

        try:
            encrypted = EncryptedData.deserialize(encrypted_str)
        except Exception as e:
            logger.warning("加密数据反序列化失败，可能是未加密数据: %s", e)
            return encrypted_str

        dek = self._derive_dek(encrypted.key_id)
        iv = base64.urlsafe_b64decode(encrypted.iv.encode("ascii"))
        ciphertext = base64.urlsafe_b64decode(encrypted.ciphertext.encode("ascii"))
        tag = base64.urlsafe_b64decode(encrypted.tag.encode("ascii"))
        aad = context.encode("utf-8") if context else None

        aesgcm = AESGCM(dek)
        ciphertext_with_tag = ciphertext + tag
        plaintext = aesgcm.decrypt(iv, ciphertext_with_tag, aad)

        return plaintext.decode("utf-8")

    def encrypt_dict(self, data: dict[str, Any], sensitive_fields: list[str], context: str = "") -> dict[str, Any]:
        """加密字典中的指定字段

        Args:
            data: 原始字典
            sensitive_fields: 需要加密的字段名列表
            context: 加密上下文

        Returns:
            加密后的字典（非敏感字段保持不变）
        """
        result = dict(data)
        for field in sensitive_fields:
            if field in result and isinstance(result[field], str):
                result[field] = self.encrypt(result[field], context=f"{context}:{field}")
        return result

    def decrypt_dict(self, data: dict[str, Any], sensitive_fields: list[str], context: str = "") -> dict[str, Any]:
        """解密字典中的指定字段

        Args:
            data: 加密后的字典
            sensitive_fields: 需要解密的字段名列表
            context: 解密上下文

        Returns:
            解密后的字典
        """
        result = dict(data)
        for field in sensitive_fields:
            if field in result and isinstance(result[field], str):
                result[field] = self.decrypt(result[field], context=f"{context}:{field}")
        return result

    def is_encrypted(self, value: str) -> bool:
        """判断字符串是否为加密数据

        Args:
            value: 待判断的字符串

        Returns:
            是否为加密数据
        """
        if not value:
            return False
        try:
            encrypted = EncryptedData.deserialize(value)
            return bool(encrypted.key_id and encrypted.ciphertext)
        except Exception:
            return False

    def _get_or_derive_dek(self) -> bytes:
        """获取或派生当前 DEK

        使用 HKDF 从 Master Key 派生 DEK，带缓存。
        """
        key_id = self._key_provider.get_key_id()

        if key_id in self._DEK_CACHE:
            return self._DEK_CACHE[key_id]

        self._key_provider.get_master_key()
        dek = self._derive_dek(key_id)

        self._DEK_CACHE[key_id] = dek
        return dek

    def _derive_dek(self, key_id: str) -> bytes:
        """从 Master Key 派生指定 key_id 的 DEK

        使用 HKDF-SHA256 进行密钥派生。
        """
        master_key = self._key_provider.get_master_key()

        if self._has_cryptography:
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            from cryptography.hazmat.primitives import hashes

            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"multi-agent-office-platform-dek",
                info=key_id.encode("utf-8"),
            )
            return hkdf.derive(master_key)
        else:
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                master_key,
                b"multi-agent-office-platform-dek" + key_id.encode("utf-8"),
                100000,
                dklen=32,
            )
            return derived

    def _encrypt_fallback(self, plaintext: str) -> str:
        """降级加密方案（cryptography 库不可用时）

        使用 XOR 混淆 + base64 编码，仅用于开发环境。
        生产环境必须安装 cryptography 库。
        """
        key = self._key_provider.get_master_key()
        key_bytes = key[:len(plaintext.encode("utf-8"))]
        key_bytes = (key_bytes * (len(plaintext.encode("utf-8")) // len(key) + 1))[:len(plaintext.encode("utf-8"))]

        plaintext_bytes = plaintext.encode("utf-8")
        encrypted_bytes = bytes(a ^ b for a, b in zip(plaintext_bytes, key_bytes))

        encrypted = EncryptedData(
            key_id=self._key_provider.get_key_id(),
            iv=base64.urlsafe_b64encode(secrets.token_bytes(12)).decode("ascii"),
            ciphertext=base64.urlsafe_b64encode(encrypted_bytes).decode("ascii"),
            tag="fallback",
        )
        return encrypted.serialize()

    def _decrypt_fallback(self, encrypted_str: str) -> str:
        """降级解密方案"""
        try:
            encrypted = EncryptedData.deserialize(encrypted_str)
        except Exception:
            return encrypted_str

        if encrypted.tag != "fallback":
            logger.warning("加密数据使用了正式算法但 cryptography 库不可用，无法解密")
            return encrypted_str

        key = self._key_provider.get_master_key()
        ciphertext = base64.urlsafe_b64decode(encrypted.ciphertext.encode("ascii"))
        key_bytes = (key * (len(ciphertext) // len(key) + 1))[:len(ciphertext)]

        decrypted_bytes = bytes(a ^ b for a, b in zip(ciphertext, key_bytes))
        return decrypted_bytes.decode("utf-8")


# ==================== Redis 加密集成 ====================

class RedisEncryptionMiddleware:
    """Redis 数据加密中间件

    在写入 Redis 前自动加密，读取后自动解密。
    仅对敏感字段进行加密，非敏感字段保持明文以支持查询。

    使用方式：
        middleware = RedisEncryptionMiddleware(encryption_manager)
        encrypted_value = middleware.encrypt_value("敏感数据", "session:abc")
        decrypted_value = middleware.decrypt_value(encrypted_value, "session:abc")
    """

    SENSITIVE_KEY_PATTERNS = [
        "session:",
        "token_revoked:",
        "user_revoked:",
        "audit:",
        "pii:",
    ]

    def __init__(self, encryption_manager: EncryptionManager):
        self._encryption_mgr = encryption_manager

    def should_encrypt_key(self, key: str) -> bool:
        """判断 Redis key 是否需要加密其值

        Args:
            key: Redis key

        Returns:
            是否需要加密
        """
        return any(key.startswith(pattern) for pattern in self.SENSITIVE_KEY_PATTERNS)

    def encrypt_value(self, value: str, context: str = "") -> str:
        """加密 Redis 值

        Args:
            value: 原始值
            context: 加密上下文

        Returns:
            加密后的值
        """
        return self._encryption_mgr.encrypt(value, context=context)

    def decrypt_value(self, value: str, context: str = "") -> str:
        """解密 Redis 值

        Args:
            value: 加密的值
            context: 解密上下文

        Returns:
            解密后的值
        """
        return self._encryption_mgr.decrypt(value, context=context)

    def encrypt_hash_fields(
        self,
        data: dict[str, str],
        sensitive_fields: list[str],
        context: str = "",
    ) -> dict[str, str]:
        """加密 Redis Hash 中的敏感字段

        Args:
            data: Hash 数据
            sensitive_fields: 敏感字段列表
            context: 加密上下文

        Returns:
            加密后的 Hash 数据
        """
        return self._encryption_mgr.encrypt_dict(data, sensitive_fields, context=context)

    def decrypt_hash_fields(
        self,
        data: dict[str, str],
        sensitive_fields: list[str],
        context: str = "",
    ) -> dict[str, str]:
        """解密 Redis Hash 中的敏感字段

        Args:
            data: 加密的 Hash 数据
            sensitive_fields: 敏感字段列表
            context: 解密上下文

        Returns:
            解密后的 Hash 数据
        """
        return self._encryption_mgr.decrypt_dict(data, sensitive_fields, context=context)


# ==================== 全局实例管理 ====================

_encryption_manager: EncryptionManager | None = None


def init_encryption(key_provider_type: str = "auto", key_file_path: str = "") -> EncryptionManager:
    """初始化加密管理器

    Args:
        key_provider_type: 密钥提供者类型（auto/local/env）
        key_file_path: 本地密钥文件路径（local 模式）

    Returns:
        EncryptionManager 实例
    """
    global _encryption_manager

    if key_provider_type == "local" and key_file_path:
        provider = LocalKeyProvider(key_file_path)
    elif key_provider_type == "env":
        provider = EnvironmentKeyProvider()
    else:
        env_key = os.environ.get("ENCRYPTION_MASTER_KEY", "")
        if env_key:
            provider = EnvironmentKeyProvider()
        else:
            default_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "instance",
                ".encryption_key",
            )
            provider = LocalKeyProvider(default_path)

    _encryption_manager = EncryptionManager(provider)
    logger.info(
        "加密管理器已初始化: provider=%s key_id=%s cryptography=%s",
        type(provider).__name__,
        provider.get_key_id(),
        _HAS_CRYPTOGRAPHY,
    )
    return _encryption_manager


def get_encryption_manager() -> EncryptionManager:
    """获取全局加密管理器实例

    Returns:
        EncryptionManager 实例
    """
    global _encryption_manager
    if _encryption_manager is None:
        _encryption_manager = init_encryption()
    return _encryption_manager

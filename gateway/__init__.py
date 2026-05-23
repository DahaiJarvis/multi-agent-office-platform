"""接入网关模块

提供多渠道适配等网关能力。

子模块:
  - adapters: 多渠道适配器（企业微信/钉钉/Web）

已整合模块（原 gateway 下的冗余文件已删除，功能迁移至对应模块）:
  - session_router -> agent/core/session_manager.py（会话转移功能）
  - rate_limiter -> api/middleware/rate_limit.py（分布式限流）
                -> api/middleware/circuit_breaker.py（熔断器）
  - load_balancer -> agent/teams/routing.py（任务路由引擎已覆盖）
  - protocol_converter -> gateway/adapters/channel_adapter.py（渠道适配器已覆盖协议转换）
"""

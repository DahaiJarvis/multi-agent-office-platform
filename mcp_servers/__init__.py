"""MCP 服务集成包

包含各企业系统的 MCP Server 实现与注册中心。

核心 MCP 服务（Phase 2）:
  - oa_server: OA 审批系统 MCP 服务
  - email_server: 邮件系统 MCP 服务
  - calendar_server: 日历系统 MCP 服务
  - crm_server: CRM 系统 MCP 服务

扩展 MCP 服务（Phase 5）:
  - approval_server: 审批系统 MCP 服务
  - im_server: IM 消息系统 MCP 服务
  - doc_server: 文档系统 MCP 服务
  - hr_server: HR 人事系统 MCP 服务
  - finance_server: 财务系统 MCP 服务
  - knowledge_server: 知识库 MCP 服务

基础设施:
  - base: 通用 API 客户端与配置
  - registry: MCP 注册中心（服务发现、健康检查）
"""

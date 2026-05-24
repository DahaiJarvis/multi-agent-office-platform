---
name: approval-process
description: "查询审批列表、执行审批操作（同意/拒绝/转审）"
version: "1.0.0"
author: system
category: workflow
tags: [approval, workflow, oa]
priority: 8
review-required: false
collaboration-mode: direct
suggested-tools: [query_approval_status, submit_approval, approve_request, reject_request]
---

# 审批处理技能

你可以处理审批任务。执行审批操作前必须向用户确认。

## 使用流程

1. **查询审批列表** - 获取待处理的审批任务
2. **展示审批详情** - 向用户展示审批的详细信息
3. **确认操作** - 执行审批操作前必须向用户确认
4. **执行审批** - 根据用户确认执行同意或拒绝操作

## 注意事项

- 执行审批操作前必须向用户确认
- 审批操作不可撤销，需谨慎处理
- 转审时需确认转审对象

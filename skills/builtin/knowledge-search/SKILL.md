---
name: knowledge-search
description: "从知识库中检索文档和信息"
version: "1.0.0"
author: system
category: knowledge
tags: [knowledge, search, rag]
priority: 6
review-required: false
collaboration-mode: direct
suggested-tools: [search_knowledge, web_search]
---

# 知识检索技能

你可以从知识库中检索信息。检索结果需标注来源。

## 使用流程

1. **理解查询意图** - 明确用户需要检索的信息类型
2. **执行检索** - 调用知识库搜索工具
3. **补充搜索** - 如知识库结果不足，使用网络搜索补充
4. **整合结果** - 将检索结果整合并标注来源
5. **展示回答** - 以清晰格式展示检索结果

## 注意事项

- 检索结果需标注来源
- 知识库和网络搜索结果需区分展示
- 如检索结果不足，需告知用户

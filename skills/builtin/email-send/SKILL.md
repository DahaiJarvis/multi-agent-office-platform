---
name: email-send
description: "撰写和发送邮件，支持收件人选择、附件添加"
version: "1.0.0"
author: system
category: communication
tags: [email, send, communication]
priority: 7
review-required: false
collaboration-mode: direct
suggested-tools: [send_email, search_emails]
---

# 邮件发送技能

你可以撰写和发送邮件。发送前需确认收件人、主题和正文。

## 使用流程

1. **确认收件人** - 向用户确认邮件的收件人地址
2. **确认主题** - 明确邮件主题
3. **撰写正文** - 根据用户要求撰写邮件正文
4. **确认发送** - 发送前向用户展示完整邮件内容并确认
5. **执行发送** - 确认后调用邮件发送工具

## 注意事项

- 发送前必须向用户确认收件人、主题和正文
- 如有附件需求，需确认附件内容

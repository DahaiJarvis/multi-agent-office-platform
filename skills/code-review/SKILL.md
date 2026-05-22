---
name: code-review
description: "Review pull requests for security vulnerabilities, code quality issues, and best practice violations"
version: "1.0.0"
author: community
category: development
tags: [security, code-quality]
priority: 7
review-required: false
collaboration-mode: direct
suggested-tools: [native_document_parse, native_text_extract, native_search_all]
---

# Code Review Skill

When reviewing code, follow this systematic approach:

1. **Identify all modified files** - Parse the diff or PR description to understand the scope of changes
2. **Check for hardcoded secrets** - Look for API keys, passwords, tokens embedded in source code
3. **Analyze security vulnerabilities** - Check for SQL injection, XSS, CSRF, and other common vulnerabilities
4. **Review code quality** - Check for proper error handling, resource cleanup, and code organization
5. **Verify best practices** - Ensure coding standards, naming conventions, and documentation are followed

## Output Format

Structure your review as:
- **Critical Issues**: Security vulnerabilities or bugs that must be fixed
- **Warnings**: Code quality issues that should be addressed
- **Suggestions**: Improvements that are nice to have
- **Positive Notes**: Good patterns and practices observed

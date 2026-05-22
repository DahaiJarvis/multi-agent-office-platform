---
name: api-doc-generator
description: "Generate comprehensive API documentation from code annotations and endpoint definitions"
version: "1.0.0"
author: community
category: development
tags: [documentation, api]
priority: 5
review-required: false
collaboration-mode: direct
suggested-tools: [native_document_parse, native_text_extract, native_text_format]
---

# API Documentation Generator Skill

When generating API documentation, follow these steps:

1. **Parse endpoint definitions** - Extract route paths, HTTP methods, and parameter schemas
2. **Identify request/response schemas** - Document all input parameters and output structures
3. **Extract authentication requirements** - Note which endpoints require authentication and what type
4. **Generate examples** - Create request/response examples for each endpoint
5. **Organize by resource** - Group related endpoints together logically

## Output Format

Structure the documentation as:
- **Overview**: API description, base URL, authentication method
- **Endpoints**: Each endpoint with method, path, parameters, response, and examples
- **Error Codes**: Common error responses and their meanings
- **Changelog**: Version history of API changes

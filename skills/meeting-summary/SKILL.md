---
name: meeting-summary
description: "Generate structured meeting summaries with action items, decisions, and key discussion points"
version: "1.0.0"
author: community
category: productivity
tags: [meeting, summary, productivity]
priority: 6
review-required: false
collaboration-mode: direct
suggested-tools: [native_document_parse, native_text_extract, native_text_format]
---

# Meeting Summary Skill

When summarizing meetings, follow this approach:

1. **Extract meeting metadata** - Date, attendees, duration, and meeting purpose
2. **Identify discussion topics** - List all major topics discussed in order
3. **Capture key decisions** - Document all decisions made during the meeting
4. **Extract action items** - List all tasks with assignees and deadlines
5. **Note open questions** - Record any unresolved issues or follow-up items

## Output Format

Structure the meeting summary as:
- **Meeting Info**: Date, attendees, duration
- **Summary**: 2-3 sentence overview of the meeting's purpose and outcome
- **Discussion Points**: Key topics discussed with brief notes
- **Decisions Made**: Numbered list of decisions with context
- **Action Items**: Table of tasks, assignees, and deadlines
- **Open Questions**: Unresolved items requiring follow-up

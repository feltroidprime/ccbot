"""Response message building for Telegram delivery.

Builds paginated response messages from Claude Code output:
  - Handles different content types (text, thinking, tool_use, tool_result)
  - Splits long messages into pages within Telegram's 4096 char limit
  - Truncates thinking content to keep messages compact

Markdown conversion is NOT done here — the send layer (message_sender,
message_queue) handles convert_markdown() so each message is converted
exactly once.

Key function:
  - build_response_parts: Build paginated response messages
"""

from ..telegram_sender import split_message
from ..transcript_parser import TranscriptParser


def build_response_parts(
    text: str,
    is_complete: bool,
    content_type: str = "text",
    role: str = "assistant",
) -> list[str]:
    """Build paginated response messages for Telegram.

    Returns a list of raw markdown strings, each within Telegram's 4096 char limit.
    Multi-part messages get a [1/N] suffix.
    Markdown-to-MarkdownV2 conversion is done by the send layer, not here.
    """
    text = text.strip()

    # User messages: add emoji prefix
    if role == "user":
        if len(text) > 3000:
            text = text[:3000] + "…"
        return [f"👤 {text}"]

    # Truncate thinking content to keep it compact
    if content_type == "thinking" and is_complete:
        start_tag = TranscriptParser.EXPANDABLE_QUOTE_START
        end_tag = TranscriptParser.EXPANDABLE_QUOTE_END
        max_thinking = 500
        if start_tag in text and end_tag in text:
            inner = text[text.index(start_tag) + len(start_tag) : text.index(end_tag)]
            if len(inner) > max_thinking:
                inner = inner[:max_thinking] + "\n\n… (thinking truncated)"
            text = start_tag + inner + end_tag
        elif len(text) > max_thinking:
            text = text[:max_thinking] + "\n\n… (thinking truncated)"

    # Thinking content gets a prefix header
    header = "∴ Thinking…\n" if content_type == "thinking" else ""

    # If text contains expandable quote sentinels, don't split --
    # the quote must stay atomic. Truncation is handled by
    # _render_expandable_quote in markdown_v2.py.
    if TranscriptParser.EXPANDABLE_QUOTE_START in text:
        return [f"{header}{text}"]

    # Split, leaving room for MarkdownV2 expansion at send layer
    max_text = 3000 - len(header)
    text_chunks = split_message(text, max_length=max_text)
    total = len(text_chunks)

    if total == 1:
        return [f"{header}{text_chunks[0]}"]

    return [
        f"{header}{chunk}\n\n[{i}/{total}]" for i, chunk in enumerate(text_chunks, 1)
    ]

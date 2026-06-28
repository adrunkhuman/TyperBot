"""Discord message length helpers."""

from __future__ import annotations

from collections.abc import Sequence

DISCORD_MESSAGE_LIMIT = 2000
CODE_FENCE = "```"


def _append_code_line_chunks(chunks: list[str], line: str, limit: int) -> None:
    payload_limit = limit - len(f"{CODE_FENCE}\n\n{CODE_FENCE}")
    for index in range(0, len(line), payload_limit):
        chunks.append(f"{CODE_FENCE}\n{line[index : index + payload_limit]}\n{CODE_FENCE}")


def _last_code_fence_index(lines: list[str]) -> int | None:
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip().startswith(CODE_FENCE):
            return index
    return None


def _flush_current_chunk(
    chunks: list[str], current_lines: list[str], *, in_code_block: bool
) -> list[str]:
    if not current_lines:
        return []
    if not in_code_block:
        chunks.append("\n".join(current_lines))
        return []

    last_fence_index = _last_code_fence_index(current_lines)
    code_payload_lines = (
        current_lines[last_fence_index + 1 :] if last_fence_index is not None else []
    )
    if last_fence_index == len(current_lines) - 1 or not any(
        line.strip() for line in code_payload_lines
    ):
        prefix = current_lines[:last_fence_index]
        if prefix:
            chunks.append("\n".join(prefix))
        return [CODE_FENCE]

    chunks.append("\n".join(current_lines) + f"\n{CODE_FENCE}")
    return [CODE_FENCE]


def chunk_discord_message(content: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split content into Discord-safe chunks.

    Returns chunks no longer than ``limit``. Code blocks are closed and reopened
    across chunks; long non-code lines are split only when needed.
    """
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    current_lines: list[str] = []
    in_code_block = False

    for line in content.splitlines():
        current = "\n".join(current_lines)
        candidate = f"{current}\n{line}" if current else line
        closing_fence_length = len(f"\n{CODE_FENCE}") if in_code_block else 0
        opens_code_block = line.strip().startswith(CODE_FENCE) and not in_code_block

        if current_lines and opens_code_block and len(candidate) + len(f"\n{CODE_FENCE}") > limit:
            current_lines = _flush_current_chunk(chunks, current_lines, in_code_block=in_code_block)

        if (
            current_lines
            and current_lines != [CODE_FENCE]
            and len(candidate) + closing_fence_length > limit
        ):
            current_lines = _flush_current_chunk(chunks, current_lines, in_code_block=in_code_block)

        line_limit = (
            limit - len(f"{CODE_FENCE}\n\n{CODE_FENCE}")
            if in_code_block and not line.strip().startswith(CODE_FENCE)
            else limit
        )
        if len(line) > line_limit:
            if current_lines:
                current_lines = _flush_current_chunk(
                    chunks, current_lines, in_code_block=in_code_block
                )
            if in_code_block:
                _append_code_line_chunks(chunks, line, limit)
                current_lines = [CODE_FENCE]
            else:
                chunks.extend(line[index : index + limit] for index in range(0, len(line), limit))
            continue

        if current_lines == [CODE_FENCE] and line.strip().startswith(CODE_FENCE):
            current_lines = []
            in_code_block = False
            continue

        current_lines.append(line)
        if line.strip().startswith(CODE_FENCE):
            in_code_block = not in_code_block

    if current_lines:
        chunks.append("\n".join(current_lines))

    return chunks


def build_discord_message_chunks(
    sections: Sequence[str],
    *,
    separator: str = "\n\n",
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> list[str]:
    """Combine sections when the joined message fits, otherwise split by section."""
    non_empty_sections = [section for section in sections if section]
    combined = separator.join(non_empty_sections)
    if len(combined) <= limit:
        return [combined]

    chunks: list[str] = []
    for section in non_empty_sections:
        chunks.extend(chunk_discord_message(section, limit=limit))
    return chunks


def _chunk_token_section(
    header: str,
    tokens: Sequence[str],
    *,
    separator: str = " ",
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> list[str]:
    """Split a header plus token list, keeping tokens intact when possible."""
    if not tokens:
        return [header]

    chunks: list[str] = []
    current = header
    for token in tokens:
        joiner = "\n" if current == header else separator
        candidate = f"{current}{joiner}{token}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current != header:
            chunks.append(current)
        current = f"{header}\n{token}" if header else token

        if len(current) > limit:
            chunks.extend(chunk_discord_message(current, limit=limit))
            current = header

    if current != header or not chunks:
        chunks.append(current)

    return chunks


def build_scoreboard_with_mentions_chunks(
    scoreboard: str,
    mentions: Sequence[str],
    *,
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> list[str]:
    """Build standings chunks, separating participant mentions when needed."""
    mention_chunks = _chunk_token_section("**Participants:**", mentions, limit=limit)
    if len(mention_chunks) == 1:
        return build_discord_message_chunks([scoreboard, mention_chunks[0]], limit=limit)

    return build_discord_message_chunks([scoreboard], limit=limit) + mention_chunks

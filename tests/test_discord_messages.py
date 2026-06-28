from typer_bot.utils import (
    DISCORD_MESSAGE_LIMIT,
    build_discord_message_chunks,
    build_scoreboard_with_mentions_chunks,
    chunk_discord_message,
)


def test_chunk_discord_message_preserves_code_block_limit_for_long_line():
    payload = "x" * 5000
    chunks = chunk_discord_message(f"```\n{payload}\n```")

    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert all(chunk.count("```") % 2 == 0 for chunk in chunks)
    assert sum(chunk.count("x") for chunk in chunks) == len(payload)


def test_chunk_discord_message_accounts_for_inserted_code_fences():
    payload = "x" * 1993
    chunks = chunk_discord_message(f"```\n{payload}\n```")

    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert "```\n```" not in chunks
    assert sum(chunk.count("x") for chunk in chunks) == len(payload)


def test_chunk_discord_message_reserves_space_before_opening_code_block():
    chunks = chunk_discord_message(f"{'A' * 1996}\n```\nx\n```")

    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert "```\n```" not in chunks


def test_chunk_discord_message_does_not_flush_title_with_empty_code_block():
    payload = "x" * 1993
    chunks = chunk_discord_message(f"Title\n```\n{payload}\n```")

    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert "Title\n```\n```" not in chunks
    assert sum(chunk.count("x") for chunk in chunks) == len(payload)


def test_chunk_discord_message_does_not_flush_blank_only_code_block():
    payload = "x" * 1993
    chunks = chunk_discord_message(f"```\n\n{payload}\n```")

    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert "```\n\n```" not in chunks
    assert sum(chunk.count("x") for chunk in chunks) == len(payload)


def test_build_discord_message_chunks_separates_oversized_sections():
    first = "A" * 1500
    second = "B" * 1500

    chunks = build_discord_message_chunks([first, second])

    assert chunks == [first, second]


def test_build_scoreboard_with_mentions_chunks_splits_long_mentions_without_breaking_tokens():
    scoreboard = "Short scoreboard"
    mentions = [f"<@{index}>" for index in range(1, 500)]

    chunks = build_scoreboard_with_mentions_chunks(scoreboard, mentions)

    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert any(chunk.startswith("**Participants:**") for chunk in chunks)
    assert f"<@{len(mentions)}>" in chunks[-1]

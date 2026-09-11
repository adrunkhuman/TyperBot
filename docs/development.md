# Development

## Run locally

```bash
uv sync --group dev
export DISCORD_TOKEN="your_test_bot_token"
uv run python -m typer_bot
```

## Manual Discord testing

Manual Discord testing can seed an isolated local database:

```powershell
$env:DISCORD_TOKEN="your_test_bot_token"
$env:DATA_DIR="./.local/manual-discord-test"
uv run python -m typer_bot.dev.seed_test_data --tester-user-id "your_discord_user_id" --guild-id "your_discord_server_id"
uv run python -m typer_bot
```

Disposable non-production deployments can auto-seed an empty database by setting `SEED_TEST_DATA=true` and `TEST_GUILD_ID`.

## Logs

Logs are plain single-line stdout. `INFO` includes startup, guild join/remove, setup state, and permission warnings; set `LOG_LEVEL=DEBUG` for fixture announcement verification details.

## Database restore

`scripts/restore_db.py` restores a SQLite backup produced after score calculation.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check typer_bot
```
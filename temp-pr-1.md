## Summary
- Move the `/admin results calculate` cooldown write to the successful scoring path so validation failures do not throttle admins.
- Add a regression test covering the failed-calculation retry case.

## Testing
- `uv run pytest tests/test_admin_commands.py -k "results_calculate"`
- `uv run ruff check typer_bot/commands/admin_commands.py tests/test_admin_commands.py`

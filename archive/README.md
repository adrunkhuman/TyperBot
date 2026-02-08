# Historical Data Archive

This folder contains SQL files that are automatically imported when the bot starts **if the database is empty**.

## How to use

1. Place your `.sql` files in this folder.
2. Ensure they follow the format shown in `example_import.sql`.
3. Start the bot with an empty database (or delete `typer.db`).
4. The bot will execute all `.sql` files in alphabetical order.

**Note:** This feature is useful for migrating data from other systems or restoring backups.

## Security Notes

Archive SQL files are validated before execution to prevent malicious operations:

1. **Sandbox Validation**: Each file is tested in a temporary transaction that is rolled back
2. **Statement Restrictions**: Only INSERT statements are permitted
3. **Blocked Operations**: ATTACH, DETACH, VACUUM, and PRAGMA statements are rejected
4. **Failure Handling**: Invalid files are skipped with an error logged; other files continue processing

**Important:** Do not include DROP, DELETE, ALTER, or other data-modifying statements in archive files.

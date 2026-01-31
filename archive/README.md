# Historical Data Archive

This folder contains SQL files that are automatically imported when the bot starts **if the database is empty**.

## How to use

1. Place your `.sql` files in this folder.
2. Ensure they follow the format shown in `example_import.sql`.
3. Start the bot with an empty database (or delete `typer.db`).
4. The bot will execute all `.sql` files in alphabetical order.

**Note:** This feature is useful for migrating data from other systems or restoring backups.

"""Shared SQL validation for backup and restore operations."""

import re


def validate_backup_sql(sql_content: str) -> bool:
    """Reject obviously unsafe SQL before backup restore or validation steps.

    This is a best-effort whitelist for the SQLite dump format we emit, not a
    real SQL parser. Real restore safety
    still depends on loading SQL into a temporary database before replacing the
    live DB.
    """
    for statement in _iter_sql_statements(sql_content):
        if not _is_allowed_backup_statement(statement):
            return False
    return True


def _is_allowed_backup_statement(statement: str) -> bool:
    normalized = statement.strip().rstrip(";").strip()
    if not normalized:
        return True

    allowed_patterns = (
        r"BEGIN\s+TRANSACTION",
        r"COMMIT",
        r"CREATE\s+TABLE\b.*",
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\b.*",
        r"INSERT\s+INTO\b.*",
        r'DELETE\s+FROM\s+["`\[]?SQLITE_SEQUENCE["`\]]?$',
    )
    return any(
        re.fullmatch(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        for pattern in allowed_patterns
    )


def _iter_sql_statements(sql_content: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    index = 0
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False

    while index < len(sql_content):
        char = sql_content[index]
        next_char = sql_content[index + 1] if index + 1 < len(sql_content) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if not in_single_quote and not in_double_quote:
            if char == "-" and next_char == "-":
                in_line_comment = True
                index += 2
                continue
            if char == "/" and next_char == "*":
                in_block_comment = True
                index += 2
                continue

        current.append(char)

        if char == "'" and not in_double_quote:
            if in_single_quote and next_char == "'":
                current.append(next_char)
                index += 2
                continue
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            if in_double_quote and next_char == '"':
                current.append(next_char)
                index += 2
                continue
            in_double_quote = not in_double_quote
        elif char == ";" and not in_single_quote and not in_double_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []

        index += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)

    return statements

"""
SQL exporter — writes CREATE TABLE + INSERT INTO statements per table.
Output files: <table>.sql

Dialect differences handled:
  MySQL  — backtick identifiers, DROP TABLE IF EXISTS, BEGIN/COMMIT transactions
  Oracle — double-quote identifiers, PL/SQL DROP block, no BEGIN for DML (autocommit),
            COMMIT at end
"""
from __future__ import annotations

import os
from typing import Callable

from db.base import BaseConnector


# ---------------------------------------------------------------------------
# Identifier quoting
# ---------------------------------------------------------------------------

def _quote_mysql(name: str) -> str:
    return f"`{name}`"


def _quote_oracle(name: str) -> str:
    return f'"{name}"'


# ---------------------------------------------------------------------------
# Value serialisation (same for both dialects)
# ---------------------------------------------------------------------------

def _sql_value(val) -> str:
    """Convert a Python value to a SQL literal."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + str(val).replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Dialect-specific DDL helpers
# ---------------------------------------------------------------------------

def _drop_mysql(quoted_table: str) -> str:
    return f"DROP TABLE IF EXISTS {quoted_table};\n\n"


def _drop_oracle(quoted_table: str) -> str:
    # Oracle has no IF EXISTS — use a PL/SQL exception block instead.
    return (
        f"BEGIN\n"
        f"   EXECUTE IMMEDIATE 'DROP TABLE {quoted_table}';\n"
        f"EXCEPTION\n"
        f"   WHEN OTHERS THEN NULL;\n"
        f"END;\n"
        f"/\n\n"
    )


def _create_table(quoted_table: str, col_defs: list[str]) -> str:
    defs = ",\n".join(col_defs)
    return f"CREATE TABLE {quoted_table} (\n{defs}\n);\n\n"


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

def export_tables_to_sql(
    connector: BaseConnector,
    tables: list[str],
    output_folder: str,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> None:
    """
    Export each table to a <table>.sql file inside output_folder.

    Each file contains:
      - DROP TABLE (dialect-safe)
      - CREATE TABLE with column definitions
      - INSERT INTO statements for all rows, wrapped in a transaction
    """
    os.makedirs(output_folder, exist_ok=True)
    n = len(tables)

    dialect = connector.config.get("dialect", "mysql").lower()
    is_oracle = dialect == "oracle"

    quote = _quote_oracle if is_oracle else _quote_mysql
    drop_stmt = _drop_oracle if is_oracle else _drop_mysql

    for i, table in enumerate(tables, start=1):
        if progress_cb:
            progress_cb(table, i, n)

        path = os.path.join(output_folder, f"{table}.sql")
        columns = connector.get_schema(table)
        col_names = [c.name for c in columns]
        quoted_table = quote(table)
        quoted_cols = ", ".join(quote(c) for c in col_names)

        with open(path, "w", encoding="utf-8") as f:
            # --- DROP ---
            f.write(drop_stmt(quoted_table))

            # --- CREATE TABLE ---
            col_defs = []
            for c in columns:
                nullable = "" if c.nullable else " NOT NULL"
                default = f" DEFAULT {_sql_value(c.default)}" if c.default else ""
                col_defs.append(f"    {quote(c.name)} {c.data_type}{nullable}{default}")
            f.write(_create_table(quoted_table, col_defs))

            # --- INSERT INTO (streamed in batches) ---
            has_rows = False
            for _, rows_batch in connector.stream_rows(table):
                if not has_rows:
                    # Oracle doesn't use BEGIN for DML — MySQL does
                    if not is_oracle:
                        f.write("BEGIN;\n")
                    has_rows = True
                for row in rows_batch:
                    values = ", ".join(_sql_value(v) for v in row)
                    f.write(
                        f"INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({values});\n"
                    )
            if has_rows:
                f.write("COMMIT;\n")

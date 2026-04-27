"""
SQL exporter — writes CREATE TABLE + INSERT INTO + TRIGGERS per table.
Output files: <table>.sql

Dialect differences handled:
  MySQL  — backtick identifiers, DROP TABLE IF EXISTS, BEGIN/COMMIT transactions
  Oracle — double-quote identifiers, PL/SQL DROP block, TO_DATE/TO_TIMESTAMP for
            date values, CLOB handling, no BEGIN for DML, COMMIT at end,
            trigger DDL appended after data
"""
from __future__ import annotations

import datetime
import decimal
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
# Value serialisation
# ---------------------------------------------------------------------------

def _sql_value_mysql(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float, decimal.Decimal)):
        return str(val)
    if isinstance(val, datetime.datetime):
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, datetime.date):
        return f"'{val.strftime('%Y-%m-%d')}'"
    if isinstance(val, datetime.timedelta):
        total = int(val.total_seconds())
        h, rem = divmod(abs(total), 3600)
        m, s = divmod(rem, 60)
        return f"'{h:02d}:{m:02d}:{s:02d}'"
    if isinstance(val, (bytes, bytearray)):
        return "0x" + val.hex()
    return "'" + str(val).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sql_value_oracle(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float, decimal.Decimal)):
        return str(val)
    if isinstance(val, datetime.datetime):
        return f"TIMESTAMP '{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, datetime.date):
        return f"TO_DATE('{val.strftime('%Y-%m-%d')}', 'YYYY-MM-DD')"
    if isinstance(val, (bytes, bytearray)):
        return "NULL /* BLOB skipped */"
    s = str(val).replace("'", "''")
    if len(s) > 32767:
        s = s[:32767]
    return f"'{s}'"


# ---------------------------------------------------------------------------
# Dialect-specific DDL helpers
# ---------------------------------------------------------------------------

def _drop_mysql(quoted_table: str) -> str:
    return f"DROP TABLE IF EXISTS {quoted_table};\n\n"


def _drop_oracle(quoted_table: str) -> str:
    return (
        f"BEGIN\n"
        f"   EXECUTE IMMEDIATE 'DROP TABLE {quoted_table} CASCADE CONSTRAINTS';\n"
        f"EXCEPTION\n"
        f"   WHEN OTHERS THEN NULL;\n"
        f"END;\n"
        f"/\n\n"
    )


def _create_table(quoted_table: str, col_defs: list[str]) -> str:
    defs = ",\n".join(col_defs)
    return f"CREATE TABLE {quoted_table} (\n{defs}\n);\n\n"


def _trigger_ddl(trigger: dict, quote) -> str:
    """
    Reconstruct CREATE OR REPLACE TRIGGER DDL from user_triggers metadata.
    """
    name   = trigger["name"]
    timing = trigger["timing"]   # e.g. BEFORE EACH ROW
    event  = trigger["event"]    # e.g. INSERT
    body   = trigger["body"]
    status = trigger["status"]

    # timing from Oracle is e.g. "BEFORE EACH ROW" or "AFTER STATEMENT"
    # We need to split into BEFORE/AFTER and ROW/STATEMENT
    parts  = timing.upper().split()
    when   = parts[0]  # BEFORE / AFTER / INSTEAD OF
    level  = "FOR EACH ROW" if "ROW" in parts else "STATEMENT"

    ddl = (
        f"CREATE OR REPLACE TRIGGER {quote(name)}\n"
        f"  {when} {event}\n"
        f"  ON {quote(trigger.get('table', ''))}\n"
        f"  {level}\n"
        f"{body}\n"
        f"/\n"
    )
    if status == "DISABLED":
        ddl += f"\nALTER TRIGGER {quote(name)} DISABLE;\n/\n"

    return ddl


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

def export_tables_to_sql(
    connector: BaseConnector,
    tables: list[str],
    output_folder: str,
    progress_cb: Callable[[str, int, int], None] | None = None,
) -> None:
    os.makedirs(output_folder, exist_ok=True)
    n = len(tables)

    dialect   = connector.config.get("dialect", "mysql").lower()
    is_oracle = dialect == "oracle"

    quote     = _quote_oracle     if is_oracle else _quote_mysql
    drop_stmt = _drop_oracle      if is_oracle else _drop_mysql
    sql_value = _sql_value_oracle if is_oracle else _sql_value_mysql

    # Check if connector supports triggers (Oracle only)
    has_trigger_support = is_oracle and hasattr(connector, "get_triggers")

    for i, table in enumerate(tables, start=1):
        if progress_cb:
            progress_cb(table, i, n)

        path = os.path.join(output_folder, f"{table}.sql")
        columns = connector.get_schema(table)
        col_names = [c.name for c in columns]
        quoted_table = quote(table)
        quoted_cols = ", ".join(quote(c) for c in col_names)

        with open(path, "w", encoding="utf-8") as f:
            # --- Header ---
            f.write(f"-- Generated by DB Exporter\n")
            f.write(f"-- Table: {table}  Dialect: {dialect}\n\n")

            # --- DROP ---
            f.write(drop_stmt(quoted_table))

            # --- CREATE TABLE ---
            col_defs = []
            for c in columns:
                nullable = "" if c.nullable else " NOT NULL"
                default  = f" DEFAULT {sql_value(c.default)}" if c.default else ""
                col_defs.append(f"    {quote(c.name)} {c.data_type}{nullable}{default}")
            f.write(_create_table(quoted_table, col_defs))

            if is_oracle:
                f.write("/\n\n")

            # --- INSERT INTO ---
            has_rows = False
            for _, rows_batch in connector.stream_rows(table):
                if not has_rows:
                    if not is_oracle:
                        f.write("BEGIN;\n")
                    has_rows = True
                for row in rows_batch:
                    values = ", ".join(sql_value(v) for v in row)
                    f.write(
                        f"INSERT INTO {quoted_table} ({quoted_cols}) VALUES ({values});\n"
                    )

            if has_rows:
                f.write("COMMIT;\n")
                if is_oracle:
                    f.write("/\n")
            else:
                f.write(f"-- No rows in {table}\n")

            # --- TRIGGERS (Oracle only) ---
            if has_trigger_support:
                try:
                    triggers = connector.get_triggers(table)
                    if triggers:
                        f.write(f"\n-- ============================================================\n")
                        f.write(f"-- Triggers for {table}\n")
                        f.write(f"-- ============================================================\n\n")
                        for t in triggers:
                            t["table"] = table  # inject table name for DDL builder
                            f.write(_trigger_ddl(t, quote))
                            f.write("\n")
                except Exception as e:
                    f.write(f"\n-- WARNING: Could not export triggers for {table}: {e}\n")

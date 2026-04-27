#!/usr/bin/env python3
"""
convert_to_oracle.py — Convert SQL export files to Oracle-compatible SQL.

Handles files exported by DB Exporter (MySQL or already-partial Oracle syntax).

Usage:
    python convert_to_oracle.py <input_folder> <output_folder>

Example:
    python convert_to_oracle.py ./exports/mysql ./exports/oracle
"""
from __future__ import annotations

import os
import re
import sys


# ---------------------------------------------------------------------------
# MySQL to Oracle type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP_PATTERNS = [
    # Integers
    (r"\bTINYINT(?:\(\d+\))?\b",              "NUMBER(3)"),
    (r"\bSMALLINT(?:\(\d+\))?\b",             "NUMBER(5)"),
    (r"\bMEDIUMINT(?:\(\d+\))?\b",            "NUMBER(7)"),
    (r"\bINT(?:EGER)?(?:\(\d+\))?\b",         "NUMBER(10)"),
    (r"\bBIGINT(?:\(\d+\))?\b",               "NUMBER(19)"),
    # Floats
    (r"\bFLOAT(?:\(\d+,\d+\))?\b",            "BINARY_FLOAT"),
    (r"\bDOUBLE(?:\s+PRECISION)?(?:\(\d+,\d+\))?\b", "BINARY_DOUBLE"),
    (r"\bDECIMAL\((\d+),(\d+)\)",             r"NUMBER(\1,\2)"),
    (r"\bNUMERIC\((\d+),(\d+)\)",             r"NUMBER(\1,\2)"),
    # Strings
    (r"\bVARCHAR\((\d+)\)",                   r"VARCHAR2(\1)"),
    (r"\bTINYTEXT\b",                         "VARCHAR2(255)"),
    (r"\bMEDIUMTEXT\b",                       "CLOB"),
    (r"\bLONGTEXT\b",                         "CLOB"),
    (r"\bTEXT\b",                             "CLOB"),
    # Binary
    (r"\bTINYBLOB\b",                         "BLOB"),
    (r"\bMEDIUMBLOB\b",                       "BLOB"),
    (r"\bLONGBLOB\b",                         "BLOB"),
    (r"\bBLOB\b",                             "BLOB"),
    (r"\bBINARY\((\d+)\)",                    r"RAW(\1)"),
    (r"\bVARBINARY\((\d+)\)",                 r"RAW(\1)"),
    # Date/time
    (r"\bDATETIME\b",                         "TIMESTAMP"),
    (r"\bDATE\b",                             "DATE"),
    (r"\bTIME\b",                             "VARCHAR2(8)"),
    (r"\bYEAR\b",                             "NUMBER(4)"),
    # Boolean
    (r"\bBOOLEAN\b",                          "NUMBER(1)"),
    (r"\bBOOL\b",                             "NUMBER(1)"),
    # MySQL-specific — strip
    (r"\bAUTO_INCREMENT\b",                   ""),
    (r"\bUNSIGNED\b",                         ""),
    (r"\bCHARACTER\s+SET\s+\S+",             ""),
    (r"\bCOLLATE\s+\S+",                     ""),
]

# Fix bare Oracle types missing length (e.g. VARCHAR2 with no parens)
_BARE_TYPE_FIXES = [
    (r"\bVARCHAR2(?!\s*\()",    "VARCHAR2(4000)"),
    (r"\bNVARCHAR2(?!\s*\()",   "NVARCHAR2(2000)"),
    (r"\bRAW(?!\s*\()",         "RAW(255)"),
]


# ---------------------------------------------------------------------------
# Conversion steps
# ---------------------------------------------------------------------------

def _normalize_line_endings(sql: str) -> str:
    return sql.replace("\r\n", "\n").replace("\r", "\n")


def _convert_identifiers(sql: str) -> str:
    """MySQL backticks → Oracle double quotes."""
    return re.sub(r"`([^`]+)`", r'"\1"', sql)


def _convert_drop(sql: str) -> str:
    """DROP TABLE IF EXISTS → Oracle PL/SQL block."""
    def replacer(m):
        table = m.group(1).strip()
        return (
            "BEGIN\n"
            f"   EXECUTE IMMEDIATE 'DROP TABLE {table} CASCADE CONSTRAINTS';\n"
            "EXCEPTION\n"
            "   WHEN OTHERS THEN NULL;\n"
            "END;\n"
            "/"
        )
    return re.sub(
        r"DROP\s+TABLE\s+IF\s+EXISTS\s+([`\"\w\.]+)\s*;",
        replacer,
        sql,
        flags=re.IGNORECASE,
    )


def _convert_types(sql: str) -> str:
    for pattern, replacement in _TYPE_MAP_PATTERNS:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
    return sql


def _fix_bare_types(sql: str) -> str:
    """Add length to bare Oracle types that are missing it."""
    for pattern, replacement in _BARE_TYPE_FIXES:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
    return sql


def _convert_create_table(sql: str) -> str:
    """Add / after CREATE TABLE block for SQL*Plus/SQLcl compatibility."""
    def replacer(m):
        block = m.group(0).rstrip()
        if not block.endswith("/"):
            block += "\n/"
        return block
    return re.sub(
        r"(CREATE\s+TABLE\s+.+?;)",
        replacer,
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _strip_engine(sql: str) -> str:
    """Remove MySQL ENGINE=... table options."""
    return re.sub(
        r"\)\s*ENGINE\s*=\s*\S+[^;]*;",
        ");",
        sql,
        flags=re.IGNORECASE,
    )


def _convert_transactions(sql: str) -> str:
    """Remove MySQL BEGIN; — Oracle doesn't use it for DML."""
    return re.sub(r"^\s*BEGIN\s*;\s*\n", "", sql, flags=re.MULTILINE | re.IGNORECASE)


def _convert_string_escapes(sql: str) -> str:
    """MySQL backslash escapes → Oracle double single-quote."""
    return sql.replace("\\'", "''")


def _convert_date_strings(sql: str) -> str:
    """
    Convert date/datetime string literals in VALUES to Oracle syntax.
    '2024-01-15 10:30:00' → TIMESTAMP '2024-01-15 10:30:00'
    '2024-01-15'          → TO_DATE('2024-01-15', 'YYYY-MM-DD')
    Only applies inside VALUES(...) to avoid touching column defaults.
    """
    # datetime first (more specific)
    sql = re.sub(
        r"'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'",
        r"TIMESTAMP '\1'",
        sql,
    )
    # date only
    sql = re.sub(
        r"'(\d{4}-\d{2}-\d{2})'",
        r"TO_DATE('\1', 'YYYY-MM-DD')",
        sql,
    )
    return sql


def _add_trigger_placeholder(sql: str, table_name: str) -> str:
    """Add a trigger placeholder comment if no triggers already present."""
    if "CREATE OR REPLACE TRIGGER" in sql.upper():
        return sql
    placeholder = (
        f"\n\n-- ============================================================\n"
        f"-- TODO: Add triggers for {table_name} here if required\n"
        f"-- Example: GUID on insert\n"
        f"-- CREATE OR REPLACE TRIGGER trg_{table_name.lower()}_guid\n"
        f"--   BEFORE INSERT ON \"{table_name}\"\n"
        f"--   FOR EACH ROW\n"
        f"-- BEGIN\n"
        f"--   IF :NEW.\"ID\" IS NULL THEN\n"
        f"--     :NEW.\"ID\" := SYS_GUID();\n"
        f"--   END IF;\n"
        f"-- END;\n"
        f"-- /\n"
        f"-- ============================================================\n"
    )
    return sql + placeholder


# ---------------------------------------------------------------------------
# File conversion
# ---------------------------------------------------------------------------

def convert_file(input_path: str, output_path: str) -> None:
    """Convert a single SQL file to Oracle SQL."""
    table_name = os.path.splitext(os.path.basename(input_path))[0]

    with open(input_path, "r", encoding="utf-8", errors="replace", newline="") as f:
        sql = f.read()

    # Step 0: normalize line endings (handles Windows CRLF files)
    sql = _normalize_line_endings(sql)

    # Step 1-9: apply conversions
    sql = _convert_identifiers(sql)
    sql = _convert_drop(sql)
    sql = _strip_engine(sql)
    sql = _convert_types(sql)
    sql = _fix_bare_types(sql)
    sql = _convert_create_table(sql)
    sql = _convert_transactions(sql)
    sql = _convert_string_escapes(sql)
    sql = _convert_date_strings(sql)
    sql = _add_trigger_placeholder(sql, table_name)

    header = (
        f"-- Converted by DB Exporter: MySQL/Partial Oracle → Oracle\n"
        f"-- Source: {os.path.basename(input_path)}\n"
        f"-- Table:  {table_name}\n\n"
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header + sql)


def convert_folder(input_folder: str, output_folder: str) -> None:
    """Convert all .sql files in input_folder to output_folder."""
    os.makedirs(output_folder, exist_ok=True)

    sql_files = sorted(f for f in os.listdir(input_folder) if f.endswith(".sql"))
    if not sql_files:
        print(f"No .sql files found in {input_folder}")
        return

    print(f"Converting {len(sql_files)} file(s)...")
    ok, errors = 0, []

    for filename in sql_files:
        input_path  = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)
        try:
            convert_file(input_path, output_path)
            print(f"  ✓  {filename}")
            ok += 1
        except Exception as e:
            print(f"  ✗  {filename} — {e}")
            errors.append((filename, str(e)))

    print(f"\nDone. {ok} converted, {len(errors)} failed.")
    if errors:
        print("\nFailed files:")
        for fname, err in errors:
            print(f"  {fname}: {err}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    input_folder  = sys.argv[1]
    output_folder = sys.argv[2]

    if not os.path.isdir(input_folder):
        print(f"Error: input folder '{input_folder}' does not exist.")
        sys.exit(1)

    convert_folder(input_folder, output_folder)

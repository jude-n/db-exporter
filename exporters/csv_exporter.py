"""
CSV exporter — writes one `<table>.csv` (data) and one `<table>.schema.csv`
(schema) per selected table.

Uses streaming via `connector.stream_rows()` so large tables don't blow up memory.
"""
import csv
import os
from typing import Callable, List, Optional


ProgressCb = Optional[Callable[[str, int, int], None]]


def _safe_filename(name: str) -> str:
    # Strip characters that are troublesome on Windows/macOS/Linux alike.
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip() or "table"


def export_schema(connector, table: str, out_path: str) -> None:
    columns = connector.get_schema(table)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["column_name", "data_type", "nullable", "default"])
        for c in columns:
            w.writerow([c.name, c.data_type, "YES" if c.nullable else "NO", c.default])


def export_data(connector, table: str, out_path: str, batch_size: int = 1000) -> int:
    total = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        header_written = False
        for columns, batch in connector.stream_rows(table, batch_size=batch_size):
            if not header_written:
                w.writerow(columns)
                header_written = True
            w.writerows(batch)
            total += len(batch)
        if not header_written:
            # Empty table — still write a header so the file isn't zero bytes.
            columns = [c.name for c in connector.get_schema(table)]
            w.writerow(columns)
    return total


def export_tables_to_csv(
    connector,
    tables: List[str],
    out_folder: str,
    batch_size: int = 1000,
    progress_cb: ProgressCb = None,
) -> None:
    os.makedirs(out_folder, exist_ok=True)
    total = len(tables)
    for i, t in enumerate(tables, start=1):
        if progress_cb:
            progress_cb(t, i, total)
        fname = _safe_filename(t)
        export_schema(connector, t, os.path.join(out_folder, f"{fname}.schema.csv"))
        export_data(
            connector, t, os.path.join(out_folder, f"{fname}.csv"), batch_size=batch_size
        )

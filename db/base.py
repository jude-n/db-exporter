"""
Abstract base class for DB connectors.

Each dialect (mysql, oracle, ...) implements:
  - connect(), close()
  - list_tables() -> list[str]
  - get_schema(table) -> list[ColumnInfo]
  - stream_rows(table, batch_size) -> generator of (columns, rows_batch)
"""
from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    default: str = ""


class BaseConnector:
    def __init__(self, config: dict):
        self.config = config
        self.conn = None

    # Context-manager support is handy for ad-hoc scripting.
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def connect(self):
        raise NotImplementedError

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def list_tables(self) -> List[str]:
        raise NotImplementedError

    def get_schema(self, table: str) -> List[ColumnInfo]:
        raise NotImplementedError

    def stream_rows(self, table: str, batch_size: int = 1000) -> Iterable[Tuple[List[str], List[tuple]]]:
        """
        Yield (columns, rows_batch). First yielded tuple's `columns` is authoritative.
        """
        raise NotImplementedError

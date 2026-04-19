"""
MySQL connector — uses mysql-connector-python.
"""
from typing import Iterable, List, Tuple

from .base import BaseConnector, ColumnInfo


class MySQLConnector(BaseConnector):
    def connect(self):
        import mysql.connector  # imported lazily so Oracle-only users don't need it

        self.conn = mysql.connector.connect(
            host=self.config["host"],
            port=int(self.config.get("port") or 3306),
            user=self.config["user"],
            password=self.config["password"],
            database=self.config["database"],
            autocommit=True,
        )

    def list_tables(self) -> List[str]:
        cur = self.conn.cursor()
        try:
            cur.execute("SHOW TABLES")
            return [row[0] for row in cur.fetchall()]
        finally:
            cur.close()

    def get_schema(self, table: str) -> List[ColumnInfo]:
        cur = self.conn.cursor()
        try:
            # Backticks guard against reserved-word table names.
            cur.execute(f"DESCRIBE `{table}`")
            rows = cur.fetchall()
            # DESCRIBE columns: Field, Type, Null, Key, Default, Extra
            return [
                ColumnInfo(
                    name=r[0],
                    data_type=r[1],
                    nullable=(str(r[2]).upper() == "YES"),
                    default="" if r[4] is None else str(r[4]),
                )
                for r in rows
            ]
        finally:
            cur.close()

    def stream_rows(self, table: str, batch_size: int = 1000) -> Iterable[Tuple[List[str], List[tuple]]]:
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT * FROM `{table}`")
            columns = [d[0] for d in cur.description]
            while True:
                batch = cur.fetchmany(batch_size)
                if not batch:
                    break
                yield columns, batch
        finally:
            cur.close()

"""
Oracle connector — uses python-oracledb in thin mode (no Oracle client required).
"""
from typing import Iterable, List, Tuple

from .base import BaseConnector, ColumnInfo


class OracleConnector(BaseConnector):
    def connect(self):
        import oracledb  # lazy import

        # `database` is treated as the Oracle Service Name.
        dsn = oracledb.makedsn(
            self.config["host"],
            int(self.config.get("port") or 1521),
            service_name=self.config["database"],
        )
        self.conn = oracledb.connect(
            user=self.config["user"],
            password=self.config["password"],
            dsn=dsn,
        )

    def list_tables(self) -> List[str]:
        cur = self.conn.cursor()
        try:
            # user_tables = tables owned by the connecting user.
            cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
            return [row[0] for row in cur.fetchall()]
        finally:
            cur.close()

    def get_schema(self, table: str) -> List[ColumnInfo]:
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                SELECT column_name, data_type, nullable, data_default
                FROM user_tab_columns
                WHERE table_name = :t
                ORDER BY column_id
                """,
                t=table.upper(),
            )
            rows = cur.fetchall()
            return [
                ColumnInfo(
                    name=r[0],
                    data_type=r[1],
                    nullable=(str(r[2]).upper() == "Y"),
                    default="" if r[3] is None else str(r[3]).strip(),
                )
                for r in rows
            ]
        finally:
            cur.close()

    def stream_rows(self, table: str, batch_size: int = 1000) -> Iterable[Tuple[List[str], List[tuple]]]:
        cur = self.conn.cursor()
        try:
            # Quoted to preserve case if needed; users typically use upper-case names.
            cur.execute(f'SELECT * FROM "{table}"')
            columns = [d[0] for d in cur.description]
            while True:
                batch = cur.fetchmany(batch_size)
                if not batch:
                    break
                yield columns, batch
        finally:
            cur.close()

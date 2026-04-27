"""
Oracle connector — uses python-oracledb in thin mode (no Oracle client required).
"""
from typing import Iterable, List, Tuple

from .base import BaseConnector, ColumnInfo


def _build_oracle_type(data_type: str, data_length, data_precision, data_scale, char_used) -> str:
    """
    Reconstruct the full Oracle column type string with precision/scale/length.
    e.g. VARCHAR2(255 CHAR), NUMBER(10,2), TIMESTAMP(6)
    """
    dt = (data_type or "").upper()

    if dt in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        length = data_length or 255
        unit = " CHAR" if char_used == "C" else ""
        return f"{dt}({length}{unit})"

    if dt == "NUMBER":
        if data_precision is not None and data_scale is not None:
            return f"NUMBER({data_precision},{data_scale})"
        if data_precision is not None:
            return f"NUMBER({data_precision})"
        return "NUMBER"

    if dt in ("FLOAT",):
        if data_precision is not None:
            return f"FLOAT({data_precision})"
        return "FLOAT"

    if dt.startswith("TIMESTAMP"):
        # data_scale holds fractional seconds precision for TIMESTAMP
        if data_scale is not None:
            return f"TIMESTAMP({data_scale})"
        return "TIMESTAMP(6)"

    if dt in ("RAW",):
        return f"RAW({data_length})" if data_length else "RAW(255)"

    # CLOB, BLOB, DATE, BINARY_FLOAT, BINARY_DOUBLE, etc — no length needed
    return dt


class OracleConnector(BaseConnector):
    def connect(self):
        import oracledb  # lazy import

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
            cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
            return [row[0] for row in cur.fetchall()]
        finally:
            cur.close()

    def get_schema(self, table: str) -> List[ColumnInfo]:
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                SELECT column_name,
                       data_type,
                       data_length,
                       data_precision,
                       data_scale,
                       char_used,
                       nullable,
                       data_default
                FROM user_tab_columns
                WHERE table_name = :t
                ORDER BY column_id
                """,
                t=table.upper(),
            )
            rows = cur.fetchall()
            result = []
            for r in rows:
                col_name, data_type, data_length, data_precision, data_scale, char_used, nullable, data_default = r
                full_type = _build_oracle_type(data_type, data_length, data_precision, data_scale, char_used)
                result.append(ColumnInfo(
                    name=col_name,
                    data_type=full_type,
                    nullable=(str(nullable).upper() == "Y"),
                    default="" if data_default is None else str(data_default).strip(),
                ))
            return result
        finally:
            cur.close()

    def stream_rows(self, table: str, batch_size: int = 1000) -> Iterable[Tuple[List[str], List[tuple]]]:
        cur = self.conn.cursor()
        try:
            cur.execute(f'SELECT * FROM "{table}"')
            columns = [d[0] for d in cur.description]
            while True:
                batch = cur.fetchmany(batch_size)
                if not batch:
                    break
                yield columns, batch
        finally:
            cur.close()

    def get_triggers(self, table: str) -> List[dict]:
        """
        Return all triggers for the given table owned by the connecting user.
        Each dict has: name, timing, event, status, body
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                SELECT trigger_name, trigger_type, triggering_event,
                       status, trigger_body
                FROM user_triggers
                WHERE table_name = :t
                ORDER BY trigger_name
                """,
                t=table.upper(),
            )
            rows = cur.fetchall()
            return [
                {
                    "name":   r[0],
                    "timing": r[1],   # e.g. BEFORE EACH ROW
                    "event":  r[2],   # e.g. INSERT
                    "status": r[3],   # ENABLED / DISABLED
                    "body":   r[4],   # PL/SQL body
                }
                for r in rows
            ]
        finally:
            cur.close()

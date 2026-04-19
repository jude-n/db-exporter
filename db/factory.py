"""
Dialect registry — add new DB types here.
"""
from .mysql_conn import MySQLConnector
from .oracle_conn import OracleConnector

_REGISTRY = {
    "mysql": MySQLConnector,
    "oracle": OracleConnector,
}

SUPPORTED_DIALECTS = list(_REGISTRY.keys())


def get_connector(dialect: str):
    try:
        return _REGISTRY[dialect.lower()]
    except KeyError as e:
        raise ValueError(f"Unsupported dialect: {dialect}. Supported: {SUPPORTED_DIALECTS}") from e

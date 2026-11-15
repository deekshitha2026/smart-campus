
import os
import psycopg


class HybridRow(tuple):
    def __new__(cls, cursor, values):
        obj = super().__new__(cls, values)
        obj.keys = [column.name for column in cursor.description]
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self.keys.index(key)
        return super().__getitem__(key)


def hybrid_row(cursor):
    def make_row(values):
        return HybridRow(cursor, values)
    return make_row


class DatabaseConnection:
    def __init__(self):
        database_url = os.environ.get("DATABASE_URL")

        if not database_url:
            raise RuntimeError("DATABASE_URL is not configured.")

        self.connection = psycopg.connect(
            database_url,
            row_factory=hybrid_row
        )

    def execute(self, query, params=()):
        query = query.replace("?", "%s")
        return self.connection.execute(query, params)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

    def rollback(self):
        self.connection.rollback()

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        pass


def connect(*args, **kwargs):
    return DatabaseConnection()


IntegrityError = psycopg.IntegrityError
Row = object

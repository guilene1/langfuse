from functools import cache
import psycopg
from app import config
 
# The fields stored with every chunk. These are what an answer cites, so a chunk retrieved
# out of context can still say where it came from and whether it is still in force.
FIELDS = [
    "document_id", "title", "filename", "section",
    "category", "region", "version", "status", "effective_date", "content",
]
 
def count():
    statement = f"""
        SELECT COUNT(*)
        FROM {config.POLICY_INDEX}
        WHERE status = 'current'
    """
    with connection().cursor() as cursor:
        try:
            cursor.execute(statement)
        except psycopg.errors.UndefinedTable:
            return 0
        return cursor.fetchone()[0]
 
@cache
def connection():
    return psycopg.connect(config.DATABASE_URL, autocommit=True)
def create_index():
    columns = ",\n ".join(f"{name} text" for name in FIELDS)
    with connection().cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cursor.execute(f"DROP TABLE IF EXISTS {config.POLICY_INDEX}")
        cursor.execute(f"""CREATE TABLE {config.POLICY_INDEX} (chunk_id bigserial PRIMARY KEY, {columns}, embedding vector({config.EMBEDDING_SIZE}))""")
        cursor.execute(f"""CREATE INDEX ON {config.POLICY_INDEX} USING hnsw (embedding vector_cosine_ops)""")
        cursor.execute(f"CREATE INDEX ON {config.POLICY_INDEX} (status)")
 
def search(vector, limit=None):
    limit = limit or config.RESULTS_PER_SEARCH
    statement = f"""SELECT {', '.join(FIELDS)}, 1 - (embedding <=> %s::vector) AS similarity FROM {config.POLICY_INDEX} WHERE status = 'current' ORDER BY embedding <=> %s::vector LIMIT %s"""
    literal = str(vector)
    with connection().cursor() as cursor:
        cursor.execute(statement, (literal, literal, limit))
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
 
def keyword_search(text, limit=None):
    limit = limit or config.RESULTS_PER_SEARCH
    statement = f"""SELECT {', '.join(FIELDS)}, ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) AS rank FROM {config.POLICY_INDEX} WHERE status = 'current' AND to_tsvector('english', content) @@ plainto_tsquery('english', %s) ORDER BY rank DESC LIMIT %s"""
    with connection().cursor() as cursor:
        cursor.execute(statement, (text, text, limit))
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
 
def add_chunks(chunks):
    columns = FIELDS + ["embedding"]
    placeholders = ", ".join(["%s"] * len(columns))
    statement = f"""INSERT INTO {config.POLICY_INDEX} ({', '.join(columns)}) VALUES ({placeholders})"""
    rows = [tuple(chunk.get(field) for field in FIELDS) + (str(chunk["embedding"]),) for chunk in chunks]
    with connection().cursor() as cursor:
        cursor.executemany(statement, rows)

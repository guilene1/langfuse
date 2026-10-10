import json
 
from app.search_index import connection
 
CHATS = "chats"
MESSAGES = "chat_messages"
 
def create_tables():
    with connection().cursor() as cursor:
        cursor.execute(f"""
CREATE TABLE IF NOT EXISTS {CHATS} (
chat_id bigserial PRIMARY KEY,
title text NOT NULL,
started_at timestamptz NOT NULL DEFAULT now(),
updated_at timestamptz NOT NULL DEFAULT now()
)
""")
        cursor.execute(f"""
CREATE TABLE IF NOT EXISTS {MESSAGES} (
message_id bigserial PRIMARY KEY,
chat_id bigint NOT NULL REFERENCES {CHATS}(chat_id) ON DELETE CASCADE,
role text NOT NULL,
content text NOT NULL,
sources text NOT NULL DEFAULT '[]',
created_at timestamptz NOT NULL DEFAULT now()
)
""")
        # Every read of a conversation is "all the messages in this chat, in order"
        cursor.execute(f"CREATE INDEX IF NOT EXISTS {MESSAGES}_chat "
            f"ON {MESSAGES} (chat_id, message_id)")
 
def start_chat(question):
    # The chat's title is just the first question, trimmed so long ones don't
    # wreck the sidebar layout.
    title = question if len(question) <= 60 else question[:57] + "..."
 
    with connection().cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {CHATS} (title) VALUES (%s) RETURNING chat_id",
            (title,),
        )
        return cursor.fetchone()[0]
def add_message(chat_id, role, content, sources=None):
    with connection().cursor() as cursor:
        cursor.execute(
            f"""
INSERT INTO {MESSAGES} (chat_id, role, content, sources)
VALUES (%s, %s, %s, %s)
""",
            (chat_id, role, content, json.dumps(sources or [])),
        )
        cursor.execute(
            f"UPDATE {CHATS} SET updated_at = now() WHERE chat_id = %s",
            (chat_id,),
        )
 
def list_chats():
    with connection().cursor() as cursor:
        cursor.execute(
            f"SELECT chat_id, title FROM {CHATS} ORDER BY updated_at DESC"
        )
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
 
def delete_chat(chat_id):
    with connection().cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {CHATS} WHERE chat_id = %s", (chat_id,)
        )
 
def get_messages(chat_id):
    # Returns the turns in the shape main.py keeps in st.session_state.messages,
    # so a reopened chat redraws exactly like a live one.
    with connection().cursor() as cursor:
        cursor.execute(
            f"""
SELECT role, content, sources
FROM {MESSAGES}
WHERE chat_id = %s
ORDER BY message_id
""",
            (chat_id,),
        )
        names = [column.name for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
 
    return [
        {
            "role": row["role"],
            "content": row["content"],
            "sources": json.loads(row["sources"]),
        }
        for row in rows
    ]

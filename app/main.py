import streamlit as st
from app import assistant, config, conversations, search_index, tracing
st.set_page_config(page_title="Northwind HR Assistant", page_icon="📄")
# st.session_state only survives as long as this browser session, so the same turns are
# written to Postgres as they happen. chat_id is the row they belong to, and is None until
# the first question starts a conversation.
st.session_state.setdefault("messages", [])
st.session_state.setdefault("chat_id", None)
EXAMPLES = [
    "How many holiday days can I carry into next year?",
    "How long is parental leave?",
    "How much sick pay do I get?",
    "Is Juneteenth a company holiday?",
]
def ask(question):
    st.session_state.messages.append({"role": "user", "content": question, "sources": []})
    if st.session_state.chat_id is None:
        st.session_state.chat_id = conversations.start_chat(question)
    conversations.add_message(st.session_state.chat_id, "user", question)
    # Send recent turns along with question, so follow-up like "what about in the UK?" makes sense.
    earlier = st.session_state.messages[:-1]
    recent = earlier[-config.MAX_HISTORY_TURNS:]
    history = []
    for message in recent:
        history.append({"role": message["role"], "content": message["content"]})
    with st.spinner("Looking through the policy documents..."):
        try:
            # One saved chat is one Langfuse session, so its turns can be read in order there
            with tracing.trace_attributes(session_id=f"chat-{st.session_state.chat_id}", tags=["ui"]):
                result = assistant.answer(question, history)
        except Exception as error:
            message = f"Something went wrong: {type(error).__name__}: {error}"
            st.session_state.messages.append({"role": "assistant", "content": message, "sources": []})
            conversations.add_message(st.session_state.chat_id, "assistant", message)
            return
    # trace_id stays in this browser session only (the database is unchanged), so the thumbs
    # under an answer can score its Langfuse trace
    st.session_state.messages.append({"role": "assistant", "content": result["answer"], "sources": result.get("sources", []),
                                      "trace_id": result.get("trace_id")})
    conversations.add_message(st.session_state.chat_id, "assistant", result["answer"], result.get("sources", []))
def record_feedback(trace_id):
    # st.feedback gives 1 for thumbs up, 0 for down, None when the choice is cleared
    choice = st.session_state[f"feedback-{trace_id}"]
    if choice is None:
        return
    # One score per answer: a fixed score id means changing your mind replaces it
    tracing.score(score_id=f"user-feedback-{trace_id}", trace_id=trace_id, name="user_feedback",
                  value=choice, data_type="BOOLEAN", comment="thumbs up" if choice else "thumbs down")
def show_feedback(message):
    # Only answers given in this session have a trace to attach a score to
    if tracing.ENABLED and message.get("trace_id"):
        st.feedback("thumbs", key=f"feedback-{message['trace_id']}",
                    on_change=record_feedback, args=(message["trace_id"],))
def open_chat(chat_id):
    st.session_state.chat_id = chat_id
    st.session_state.messages = conversations.get_messages(chat_id)
 
def show_sources(sources):
    if not sources:
        return
 
    with st.expander(f"Sources ({len(sources)})"):
        for source in sorted(sources, key=lambda s: s["number"]):
            header = f"**[{source['number']}] {source.get('title', 'Untitled')}**"
            if source.get("section"):
                header += f" — {source['section']}"
            st.markdown(header)
 
            detail = []
            if source.get("filename"):
                detail.append(source["filename"])
            if source.get("region") and source["region"] != "global":
                detail.append(f"{source['region'].upper()} only")
            if source.get("similarity") is not None:
                detail.append(f"similarity {source['similarity']:.2f}")
 
            if detail:
                st.caption(" · ".join(detail))
 
def sidebar():
    if st.sidebar.button("New chat", use_container_width=True):
        st.session_state.chat_id = None
        st.session_state.messages = []
        st.rerun()
 
    chats = conversations.list_chats()
    if not chats:
        return
 
    st.sidebar.caption("Earlier chats")
    for chat in chats:
        current = chat["chat_id"] == st.session_state.chat_id
        # Streamlit needs a key per button, because the labels repeat
        if st.sidebar.button(chat["title"], key=f"chat-{chat['chat_id']}",
                use_container_width=True,
                type="primary" if current else "secondary"):
            open_chat(chat["chat_id"])
            st.rerun()
 
    if st.session_state.chat_id is not None:
        if st.sidebar.button("Delete this chat", use_container_width=True):
            conversations.delete_chat(st.session_state.chat_id)
            st.session_state.chat_id = None
            st.session_state.messages = []
            st.rerun()
st.title("Northwind HR Assistant")
 
# An assistant that answers "I could not find anything" to every question usually has an
# empty index rather than a broken model, so say that plainly rather than leaving somebody to
# guess. When it is working, this says nothing at all.
try:
    indexed = search_index.count()
    # Cheap and idempotent: the two chat tables are created the first time the page is
    # opened after a fresh database, and skipped every time after that.
    conversations.create_tables()
except Exception as error:
    indexed = 0
    st.error(f"Cannot reach the database: {error}")
 
if indexed == 0:
    st.error("No policy documents are indexed. Run: docker compose run --rm ingest")
 
if not st.session_state.messages:
    st.caption(
        "I answer questions about Northwind policy from the HR document library. "
        "I cannot see your own record: for your leave balance, pay or review, "
        "check Workday or ask the HR service desk."
    )
    left, right = st.columns(2)
    for number, example in enumerate(EXAMPLES):
        if number % 2 == 0:
            column = left
        else:
            column = right
 
        if column.button(example, use_container_width=True):
            ask(example)
            st.rerun()
 
# Redraw the conversation. st.chat_message supplies the avatars.
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        show_sources(message["sources"])
        show_feedback(message)

question = st.chat_input("Ask about holiday, sick pay, parental leave, expenses...")
if question:
    ask(question)
    st.rerun()
 
sidebar()

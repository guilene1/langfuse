import re
from functools import cache
 
from app import config, retrieval, tracing
from app.tracing import observe
import anthropic
 
@cache
def _claude():
    return anthropic.Anthropic()
 
SYSTEM_PROMPT = """You are the Northwind Systems HR policy assistant.
You answer questions about company policy using the numbered policy excerpts supplied with each question. Employees of Northwind use you to look things up without waiting for the HR service desk.
HOW TO ANSWER
- Use only the excerpts provided. Never fill a gap from your own knowledge of employment law or of how other companies do things.
- Cite the bracketed number of every excerpt you used, like [1] or [2]. Cite as you go, not in a list at the end.
- Be brief. Two or three sentences answers most questions. Give the specific number, day count or deadline rather than describing it.
- If the excerpts disagree, say so and give both, with their document titles.
WHEN THE EXCERPTS DO NOT ANSWER THE QUESTION
Say that plainly and point the person at the HR service desk, hr-help@northwind.example. Do not guess, and do not offer a general answer about what companies usually do.
Cite nothing in that case. A citation under "the policy library does not cover this" points at a document that does not answer the question, which is worse than no citation at all.
WHAT YOU CANNOT KNOW
You have no access to anyone's personal record. You cannot see leave balances, pay, benefit elections, performance reviews, absence history or any case in progress. You do not know who is asking.
 
When a question is about someone's own situation - "how many days do I have left", "when is my review", "why was my claim rejected", "what is my notice period" - do BOTH of these:
1. give the policy that applies, with its citation, because that part is usually useful
2. say clearly that the personal detail is not something you can see, and where to get it: Workday for balances and payslips, the HR service desk for anything else
Never estimate, guess or illustrate a personal figure, not even as an example.
 
POLICY THAT DEPENDS ON LOCATION
Several policies differ by country: parental leave, sick pay, notice periods and probation. If the question does not say which country and the excerpts cover more than one, give the answer for each, or ask which one applies. Do not silently pick one.
 
VERSIONS
Every excerpt states the version and effective date of the document it came from. Only current documents are searched, so you will not normally see a superseded one. If an excerpt says SUPERSEDED, do not rely on it, and say that a newer version exists."""
GREETING_WORDS = {
"hi", "hello", "hey", "yo", "hiya", "howdy", "greetings",
"morning", "afternoon", "evening",
}
ACKNOWLEDGEMENT_WORDS = {
"thanks", "thank", "thx", "ty", "ta", "cheers", "appreciated",
"ok", "okay", "kk", "cool", "great", "perfect", "brilliant",
"bye", "goodbye", "later", "farewell",
}
# Words that carry no topic. They may appear alongside a greeting without making it a question
FILLER_WORDS = {
"a", "again", "all", "am", "and", "are", "bunch", "day", "doing", "everyone",
"folks", "friend", "good", "guys", "how", "i", "indeed", "is", "it", "lot", "lots",
"madam", "many", "mate", "me", "much", "my", "nice", "please", "sir", "so", "team",
"the", "there", "to", "today", "u", "very", "well", "yes", "you", "your",
}
# Phrases about the assistant itself, which deserve the same answer as a greeting
ABOUT_ME = {"who are you", "what are you", "what can you do", "what do you do", "help"}
GREETING = ("Hello. I answer questions about Northwind policy, using the HR document library. "
            "Ask me anything the handbook covers and I will quote the source.")
FOLLOW_UP_STARTS = ("what about", "how about", "and ", "but ", "or ", "why", "what if", "then ", "in ", "for ", "same", "does that", "is that", "what else")
ACKNOWLEDGEMENT = "You're welcome. Let me know if you have another policy question."
def _messages(question, history, excerpts):
    messages = []
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": f"POLICY EXCERPTS:\n\n{excerpts}\n\nQUESTION: {question}"})
    return messages
def small_talk_reply(question):
    cleaned = re.sub(r"[^\w\s]", " ", question).strip().lower()
    words = cleaned.split()
    if not words:
        return GREETING
    if " ".join(words) in ABOUT_ME:
        return GREETING
    # Anything long enough to be a real question is treated as one, whatever words it uses
    if len(words) > 6:
        return None
    social = GREETING_WORDS | ACKNOWLEDGEMENT_WORDS | FILLER_WORDS
    if not all(word in social for word in words):
        return None
    if any(word in GREETING_WORDS for word in words):
        return GREETING
    if any(word in ACKNOWLEDGEMENT_WORDS for word in words):
        return ACKNOWLEDGEMENT
    # Only filler, like "how are you" - closer to a greeting than to a question
    return GREETING
def is_follow_up(question):
    words = question.split()
    if len(words) < 3:
        return True
    lowered = question.strip().lower()
    for start in FOLLOW_UP_STARTS:
        if lowered.startswith(start):
            return True
    return False
def search_text(question, history):
    if not history:
        return question
 
    topic = ""
    for message in history:
        if message["role"] == "user" and not is_follow_up(message["content"]):
            topic = message["content"]
 
    if topic == "" or not is_follow_up(question):
        return question
    return topic + " " + question
 
@observe(name="claude-answer", as_type="generation", capture_input=False)
def _generate(messages):
    # The model call on its own, so Langfuse can show its prompt, tokens, cost and latency.
    # The input is set here rather than captured, so it includes the system prompt.
    tracing.update_generation(
        input={"system": SYSTEM_PROMPT, "messages": messages},
        model=config.CLAUDE_MODEL,
        model_parameters={"max_tokens": config.MAX_ANSWER_TOKENS},
    )
    response = _claude().messages.create(
        model=config.CLAUDE_MODEL, max_tokens=config.MAX_ANSWER_TOKENS,
        system=SYSTEM_PROMPT, messages=messages,
    )
    # Langfuse prices these from its own table for the model, so no cost is hard-coded here
    usage = response.usage
    tracing.update_generation(
        model=response.model,
        usage_details={
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
        },
        metadata={"stop_reason": response.stop_reason, "request_id": response._request_id},
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()

def _finish(result, route):
    # Which of the three paths produced the answer, so traces can be filtered by it, and the
    # trace id, so feedback on this answer can be attached to its trace later
    tracing.update_current(metadata={"route": route})
    result["trace_id"] = tracing.current_trace_id()
    return result

@observe(name="hr-question", as_type="chain")
def answer(question, history=None):
    social = small_talk_reply(question)
    if social:
        return _finish({"answer": social, "sources": [], "found_policy": False}, "small_talk")

    chunks = retrieval.search_policies(search_text(question, history or []))
    if not chunks:
        return _finish({
            "answer": (
                "I could not find anything about that in the Northwind policy documents. "
                "The HR service desk can help: hr-help@northwind.example."
            ),
            "sources": [], "found_policy": False,
        }, "no_policy")

    text = _generate(_messages(question, history or [], retrieval.format_excerpts(chunks)))

    cited_numbers = []
    for number in re.findall(r"\[(\d+)\]", text):
        cited_numbers.append(int(number))
 
    sources = []
    for source in retrieval.cite(chunks):
        if source["number"] in cited_numbers:
            sources.append(source)
 
    new_number = {}
    for position, source in enumerate(sources, start=1):
        new_number[source["number"]] = position
 
    for old, new in new_number.items():
        text = text.replace(f"[{old}]", f"[{new}]")
 
    for source in sources:
        source["number"] = new_number[source["number"]]
 
    return _finish({"answer": text.strip(), "sources": sources, "found_policy": bool(sources)}, "answered")

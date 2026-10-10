"""LLM-as-a-judge: a second, cheaper model grades the assistant's answers.

Two kinds of judge are used in the lab:

- The correctness judge below runs in this code, during an experiment, because it needs each
  test case's expectations, which only the dataset has.
- The faithfulness, citation and relevance judges run inside Langfuse (MANAGED_JUDGES), on every
  `claude-answer` observation, because that observation holds both the excerpts Claude was
  given and the answer it wrote. experiment.py creates them.

Every judge writes its reasoning before its verdict, and the reasoning is kept with the score.
"""
import json
import os
from functools import cache

import anthropic

from app import tracing
from app.tracing import observe

# Claude Haiku 5.5 costs a tenth of the assistant's model per token. Set JUDGE_MODEL to
# claude-haiku-4-5 to judge with the assistant's own model instead.
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-haiku-5-5")

VERDICT_SCORES = {"correct": 1.0, "partially_correct": 0.5, "incorrect": 0.0}

CORRECTNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICT_SCORES)},
    },
    "required": ["reasoning", "verdict"],
    "additionalProperties": False,
}

CORRECTNESS_PROMPT = """You are grading one answer from Northwind's HR policy assistant against what a correct answer must do. The assistant answers employees' questions from company policy documents.

{conversation}QUESTION: {question}

ANSWER UNDER TEST:
{answer}

DOCUMENTS THE ANSWER CITED: {sources}

WHAT A CORRECT ANSWER MUST DO
{requirements}

Judge the meaning, not the exact wording: "five days" counts as "5", and extra correct detail is fine.
- correct: meets every requirement above.
- partially_correct: right in substance, but misses one requirement or adds a claim the requirements do not support.
- incorrect: states a wrong fact, invents a personal figure, relies on an outdated policy, or answers when it should have said it cannot.

Give your reasoning in at most three sentences, then the verdict."""


@cache
def _client():
    return anthropic.Anthropic()


def _requirements(case):
    lines = [f"- Why this test exists: {case.get('why', 'not given')}"]
    if case.get("must_contain"):
        quantifier = "at least one of" if case.get("any_of") else "all of"
        lines.append(f"- It must state {quantifier}: {', '.join(case['must_contain'])}")
    if case.get("must_not_contain"):
        lines.append(f"- It must not say: {', '.join(case['must_not_contain'])}")
    if case.get("must_disclaim"):
        lines.append("- It must say plainly that it cannot see the person's own records")
    if case.get("expect_no_sources"):
        lines.append("- It must cite no documents, because none of them answers this")
    return "\n".join(lines)


def correctness_prompt(case, answer):
    earlier = case.get("after") or []
    conversation = ("EARLIER QUESTIONS IN THIS CONVERSATION: " + " / ".join(earlier) + "\n") if earlier else ""
    sources = ", ".join(s["title"] for s in answer.get("sources", [])) or "none"
    return CORRECTNESS_PROMPT.format(
        conversation=conversation, question=case["question"], answer=answer["answer"],
        sources=sources, requirements=_requirements(case),
    )


@observe(name="correctness-judge", as_type="generation", capture_input=False)
def judge_correctness(case, answer):
    """Returns {"reasoning": ..., "verdict": ..., "score": ...} for one answer to one test case."""
    prompt = correctness_prompt(case, answer)
    output_config = {"format": {"type": "json_schema", "schema": CORRECTNESS_SCHEMA}}
    # Effort exists from Haiku 5.5 on. "low" keeps the judge short and cheap: on a grading task
    # this size it usually answers without spending any tokens on thinking.
    if JUDGE_MODEL != "claude-haiku-4-5":
        output_config["effort"] = "low"
    tracing.update_generation(input=prompt, model=JUDGE_MODEL, model_parameters={"max_tokens": 1024})

    response = _client().messages.create(
        model=JUDGE_MODEL, max_tokens=1024, output_config=output_config,
        messages=[{"role": "user", "content": prompt}],
    )
    tracing.update_generation(
        model=response.model,
        usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        metadata={"stop_reason": response.stop_reason},
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"judge stopped with {response.stop_reason}")
    # A response can open with thinking blocks, so look for the text block rather than taking the first
    result = json.loads(next(block.text for block in response.content if block.type == "text"))
    result["score"] = VERDICT_SCORES[result["verdict"]]
    return result


# The judges that run inside Langfuse. Each sees one `claude-answer` observation: {{context}} is
# the messages Claude was sent (the numbered excerpts and the question), {{answer}} what it wrote.
# The citation numbers in {{answer}} are Claude's own, so they match the excerpt numbers.
#
# The instructions come first and the data last, inside tags. A first version put instructions
# after the answer, and the judge read them as part of the answer it was grading.
_DATA = """<conversation>
{{context}}
</conversation>

<answer>
{{answer}}
</answer>"""

MANAGED_JUDGES = [
    {
        "name": "faithfulness",
        "data_type": "NUMERIC",
        "prompt": """You check whether an HR assistant's answer is supported by the policy excerpts it was given.

Below, <conversation> holds the messages the assistant received; the last one contains the numbered POLICY EXCERPTS and the QUESTION. <answer> holds the assistant's reply, and only that reply is being graded.

Check every factual claim in the answer: numbers, durations, deadlines, eligibility, amounts. A claim is supported if an excerpt states it or it follows directly from one. Saying that the documents do not cover something, or sending the person to the HR service desk (hr-help@northwind.example) or Workday, needs no support.

""" + _DATA,
        "value_instructions": "1 if every claim is supported. 0.5 if one minor detail is unsupported. 0 if any number, deadline or entitlement is unsupported or contradicts the excerpts.",
        "reasoning_instructions": "Name any unsupported claim, or say that all claims are supported. At most three sentences.",
    },
    {
        "name": "citation_accuracy",
        "data_type": "BOOLEAN",
        "prompt": """You check the citations in an HR assistant's answer.

Below, <conversation> holds the messages the assistant received; the last one contains the numbered POLICY EXCERPTS, each starting with its number in brackets such as [1], and the QUESTION. <answer> holds the assistant's reply, and only that reply is being graded. The reply cites excerpts with the same bracketed numbers.

The citations are accurate when every bracketed number in the answer points to an excerpt that really contains the fact it is attached to, and every specific number, duration or deadline in the answer carries a citation. An answer that says the excerpts do not cover the question and cites nothing is also accurate.

""" + _DATA,
        "value_instructions": "true if the citations are accurate as defined above, otherwise false.",
        "reasoning_instructions": "Go through the citations in the answer and name any that points at the wrong excerpt, or any figure without one; if all are right, say which excerpt supports which fact. At most three sentences.",
    },
    {
        "name": "relevance",
        "data_type": "BOOLEAN",
        "prompt": """You check whether an HR assistant's answer addresses the question it was asked.

Below, <conversation> holds the messages the assistant received: any earlier turns, then a message with the numbered POLICY EXCERPTS and the QUESTION. <answer> holds the assistant's reply, and only that reply is being graded.

For a follow-up such as "what about in Berlin", the question is the earlier one it continues. An answer that says the documents do not cover the question, or that a personal detail must be checked in Workday, is relevant. An answer is not relevant if it answers a different question or fills space with unrelated policy.

""" + _DATA,
        "value_instructions": "true if the answer addresses the question asked, otherwise false.",
        "reasoning_instructions": "Say in one or two sentences what the question asked and whether the answer addressed it.",
    },
]

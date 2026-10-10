"""Langfuse tracing, switched on only when both Langfuse keys are set.

The other modules import observe and the helpers below from here, never from langfuse
directly, so this file alone decides whether anything is sent. Without keys every helper
does nothing and the assistant behaves exactly as it does without Langfuse.
"""
import os

from app import config

ENABLED = bool(config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY)

if not ENABLED:
    # The SDK only switches itself off when a key is missing altogether. docker compose passes
    # an empty string for an unset key, which the SDK would try to use, so say it explicitly.
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"

from langfuse import get_client, observe, propagate_attributes  # noqa: E402  (must follow the switch above)

langfuse = get_client()

__all__ = ["ENABLED", "observe", "trace_attributes", "update_current",
           "update_generation", "current_trace_id", "score", "flush"]


def trace_attributes(**attributes):
    # Session, tags and metadata for the trace of every observation inside the with block
    return propagate_attributes(**attributes)


def update_current(**fields):
    # Adds output or metadata to the span or chain that is running now
    langfuse.update_current_span(**fields)


def update_generation(**fields):
    # Adds the model, token usage and output to the model call that is running now
    langfuse.update_current_generation(**fields)


def current_trace_id():
    # None when tracing is off
    return langfuse.get_current_trace_id()


def score(**fields):
    # Attaches a score (a test result, a judge's grade, a user's thumbs-up) to a trace
    langfuse.create_score(**fields)


def flush():
    # Send everything still buffered. Needed before a short-lived process such as evaluate exits.
    langfuse.flush()

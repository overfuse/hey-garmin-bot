"""Environment-driven tunables for the workout AI layer, read in one place.

LLM_TIMEOUT_S bounds the provider call twice on purpose: the gate's wait_for is
the hard stop that frees the concurrency slot, and the same value is passed as
each SDK client's timeout so the underlying HTTP request gives up in step with
it (the SDKs default to 600s otherwise).
"""

import os

from dotenv import load_dotenv

load_dotenv()

# Provider selection. Both API keys can live in .env; only the selected
# provider's key is needed at runtime.
#   WORKOUT_AI_PROVIDER  "claude" | "openai"   (default: openai)
#   WORKOUT_AI_MODEL     optional override of the provider's default model
PROVIDER = os.environ.get("WORKOUT_AI_PROVIDER", "openai").lower()
MODEL = os.environ.get("WORKOUT_AI_MODEL")

LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "45"))
LLM_QUEUE_WAIT_S = float(os.getenv("LLM_QUEUE_WAIT_S", "10"))

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
#   WORKOUT_AI_PROVIDER  "claude" | "openai"   (default: claude)
#   WORKOUT_AI_MODEL     optional override of the provider's default model
PROVIDER = os.environ.get("WORKOUT_AI_PROVIDER", "claude").lower()
MODEL = os.environ.get("WORKOUT_AI_MODEL")

LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "45"))
LLM_QUEUE_WAIT_S = float(os.getenv("LLM_QUEUE_WAIT_S", "10"))

# Not-a-workout pre-gate (TypeSafe Jev via OpenRouter, see jev.py). OFF unless
# OPENROUTER_API_KEY is set: no key means no gate at all — every message goes
# to the LLM as before, whatever the user's reject_non_workouts pref.
# Threshold from the 2026-10-24 PoC: workouts scored >= 0.59, chatter <= 0.09,
# so 0.3 leaves margin on both sides. Latency was p50 0.21s / max 0.41s, so 2s
# only trips on a genuinely sick endpoint.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
JEV_ENABLED = bool(OPENROUTER_API_KEY)
JEV_MODEL = os.getenv("JEV_MODEL", "typesafe/jev-1.14")
JEV_REJECT_BELOW = float(os.getenv("JEV_REJECT_BELOW", "0.3"))
JEV_TIMEOUT_S = float(os.getenv("JEV_TIMEOUT_S", "2"))
JEV_BREAKER_FAILURES = int(os.getenv("JEV_BREAKER_FAILURES", "3"))
JEV_BREAKER_COOLDOWN_S = float(os.getenv("JEV_BREAKER_COOLDOWN_S", "300"))

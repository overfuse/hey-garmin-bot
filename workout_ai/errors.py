"""Lives in its own module so providers can import it without a cycle
(__init__ imports the provider registry at module top)."""


class WorkoutAIConfigError(Exception):
    """Our misconfiguration — unknown provider, missing API key.

    Raised strictly BEFORE any provider request is issued, so callers must
    refund the user's quota unit: nothing was billed, and the failure is ours,
    not their input's.
    """


class LLMQuotaExhausted(Exception):
    """The provider account is out of credits (e.g. OpenAI `insufficient_quota`).

    A request WAS issued, but the provider rejected it at the door — no tokens
    were consumed, nothing was billed. Like WorkoutAIConfigError this is our
    operational failure, not the user's input: callers must refund the user's
    quota unit and must not blame the workout text.
    """


class LLMBusy(Exception):
    """Every LLM slot was occupied and none freed up within LLM_QUEUE_WAIT_S.

    Deliberately NOT a TimeoutError subclass: no provider call was made, so callers
    must not report this as "parsing timed out" or bill it as a spent attempt.
    """

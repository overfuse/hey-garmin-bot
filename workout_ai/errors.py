"""Lives in its own module so providers can import it without a cycle
(__init__ imports the provider registry at module top)."""


class WorkoutAIConfigError(Exception):
    """Our misconfiguration — unknown provider, missing API key.

    Raised strictly BEFORE any provider request is issued, so callers must
    refund the user's quota unit: nothing was billed, and the failure is ours,
    not their input's.
    """

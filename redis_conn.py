"""Single shared Redis handle.

rate_limiter.init() owns construction and the failure policy (REDIS_URL
required unless RATE_LIMIT_DISABLED=1, ping before publishing) and installs
the client here. Everything that needs Redis — the limiter itself, session.py's
login handshake — reads `client` from this module instead of reaching into
another module's globals. None means Redis is absent (RATE_LIMIT_DISABLED
local dev) and callers fall back or fail per their own policy.
"""

from typing import Any

client: Any = None

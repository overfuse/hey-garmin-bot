from cachetools import TTLCache

# Temporary in-memory sessions for raw credentials (expires after 5 minutes)
temp_sessions = TTLCache(maxsize=1000, ttl=300)
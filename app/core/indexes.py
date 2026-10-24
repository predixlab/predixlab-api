"""Index specifications shared by database setup and queries."""

# Cosmos DB RU requires sort paths and directions to exactly match the compound
# index, including equality-filtered fields.
AGENT_API_KEYS_LIST_INDEX: list[tuple[str, int]] = [
    ("user_id", 1), ("created_at", -1), ("_id", -1),
]

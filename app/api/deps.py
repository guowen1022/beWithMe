"""FastAPI dependencies for user resolution."""
from uuid import UUID
from fastapi import Header


async def get_current_user_id(
    x_user_id: UUID = Header(...),
) -> UUID:
    """Extract user_id from the X-User-Id request header."""
    return x_user_id

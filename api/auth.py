"""
Authentication and authorization helpers.

Security rules:

- The Supabase service-role key is SERVER ONLY.
- Clients authenticate using a Supabase JWT.
- Never trust user_id supplied by the frontend.
- Never expose JWT validation errors or internal exceptions.
"""

import logging

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from db.client import supabase


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bearer authentication
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> str:
    """
    Validate the Supabase access token and return the authenticated user ID.

    IMPORTANT:
    Never accept user_id from request body/form/query parameters.

    The authenticated identity comes exclusively from the JWT.
    """

    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    token = credentials.credentials.strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    try:
        response = supabase.auth.get_user(token)

        user = response.user if response else None

        if user is None or not user.id:
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
            )

        return str(user.id)

    except HTTPException:
        raise

    except Exception:
        logger.warning("Supabase authentication failed")

        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
        )
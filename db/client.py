# """
# Centralized Supabase server client.

# This module is SERVER ONLY.

# NEVER expose the service-role key to:
# - React
# - browser JavaScript
# - client-side environment variables
# - API responses
# """

# import os

# from dotenv import load_dotenv
# from supabase import Client, create_client


# load_dotenv()


# SUPABASE_URL = os.getenv("SUPABASE_URL")
# SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


# if not SUPABASE_URL:
#     raise RuntimeError("SUPABASE_URL is not configured")

# if not SUPABASE_SERVICE_ROLE_KEY:
#     raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not configured")


# supabase: Client = create_client(
#     SUPABASE_URL,
#     SUPABASE_SERVICE_ROLE_KEY,
# )



"""
Centralized Supabase server client.

SECURITY:
- Uses service-role key.
- This file must NEVER be imported by frontend code.
- The service-role key must NEVER be returned in an API response.
"""

import os

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is not configured")

if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not configured")


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
)
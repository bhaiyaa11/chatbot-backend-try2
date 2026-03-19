

# import os
# import logging
# from supabase import create_client
# from dotenv import load_dotenv

# logger = logging.getLogger(__name__)
# load_dotenv()

# supabase_client = create_client(
#     os.getenv("SUPABASE_URL"),
#     os.getenv("SUPABASE_KEY")
# )




# async def get_few_shot_examples(limit: int = 2) -> str:
#     """Fetch top rated scripts to inject as examples into prompts."""
#     try:
#         result = supabase_client.table("training_data") \
#             .select("prompt, output") \
#             .eq("rating", 1) \
#             .order("created_at", desc=True) \
#             .limit(limit) \
#             .execute()

#         if not result.data:
#             return ""

#         examples = []
#         for i, row in enumerate(result.data):
#             examples.append(
#                 f"--- Example {i+1} ---\n"
#                 f"User asked: {row['prompt']}\n"
#                 f"Good output:\n{row['output']}"
#             )

#         return "\n\n".join(examples)

#     except Exception as e:
#         # Never crash the pipeline if DB is unavailable
#         logger.warning(f"[FewShot] Could not fetch examples: {e}")
#         return ""




import os
import logging
from supabase import create_client
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

_supabase_client = None


def _get_supabase_client():
    """
    Lazy initialization so serverless import never crashes.
    Functionality remains identical.
    """
    global _supabase_client

    if _supabase_client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")

        if not url or not key:
            raise RuntimeError("SUPABASE_URL or SUPABASE_KEY not set")

        _supabase_client = create_client(url, key)

    return _supabase_client


async def get_few_shot_examples(limit: int = 2) -> str:
    """Fetch top rated scripts to inject as examples into prompts."""
    try:
        supabase_client = _get_supabase_client()

        result = (
            supabase_client.table("training_data")
            .select("prompt, output")
            .eq("rating", 1)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        if not result.data:
            return ""

        examples = []
        for i, row in enumerate(result.data):
            examples.append(
                f"--- Example {i+1} ---\n"
                f"User asked: {row['prompt']}\n"
                f"Good output:\n{row['output']}"
            )

        return "\n\n".join(examples)

    except Exception as e:
        # Never crash pipeline
        logger.warning(f"[FewShot] Could not fetch examples: {e}")
        return ""
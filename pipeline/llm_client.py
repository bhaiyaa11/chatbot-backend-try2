

# adding gemini3 location preview




import json, time, asyncio, logging, random
from google import genai
from config import MODEL_ENDPOINTS, MAX_RETRIES
from pipeline.cache import cache

logger = logging.getLogger(__name__)

# --------------------------------------------------
# Location map — only CRITIC uses global
# --------------------------------------------------
_STAGE_LOCATIONS = {
    "VOICE_OVER": "us-central1",
    "VISUALS":    "us-central1",
    "CRITIC":     "global",
}

def _get_client_and_model(stage: str, endpoint: str):
    location = _STAGE_LOCATIONS.get(stage, "us-central1")
    client = genai.Client(vertexai=True, project="poc-script-genai", location=location)
    return client, endpoint


def _parse_json_response(text: str) -> dict:
    """Strip markdown fences if model ignores instructions, then parse."""
    text = text.strip()
    if text.startswith("```"):
        # strip opening fence + optional 'json' label
        lines = text.split("\n")
        lines = lines[1:]  # remove ```json line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # remove closing ```
        text = "\n".join(lines)
    return json.loads(text.strip())


def _is_quota_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ["429", "quota", "resource_exhausted", "rate limit"])


async def call_llm(stage: str, contents: list) -> tuple[dict, int, bool]:
    """
    Returns (parsed_json, attempts_used, cache_hit).
    Tries primary endpoint first, falls back to secondary on quota errors.
    Raises RuntimeError if all endpoints and retries fail.
    """
    # ── Cache check ──────────────────────────────────────────────
    cached = cache.get(stage, contents)
    if cached:
        return cached, 0, True

    endpoints = MODEL_ENDPOINTS[stage]  # list: [primary, fallback, ...]
    total_attempts = 0

    for endpoint_idx, endpoint in enumerate(endpoints):
        client, model_id = _get_client_and_model(stage, endpoint)
        is_fallback = endpoint_idx > 0

        if is_fallback:
            logger.warning(f"[{stage}] Switching to fallback endpoint: {endpoint}")

        for attempt in range(1, MAX_RETRIES + 1):
            total_attempts += 1
            try:
                response = await client.aio.models.generate_content(
                    model=model_id,
                    contents=contents
                )
                parsed = _parse_json_response(response.text or "")

                # ── Store in cache on success ─────────────────
                cache.set(stage, contents, parsed)

                logger.info(f"[{stage}] Success on attempt {total_attempts} via {endpoint}")
                return parsed, total_attempts, False

            except json.JSONDecodeError as e:
                logger.warning(f"[{stage}] JSON parse failed attempt {total_attempts}: {e}")
                # JSON errors are model output issues — retry same endpoint
                wait = attempt + random.uniform(0, 0.5)
                await asyncio.sleep(wait)

            except Exception as e:
                if _is_quota_error(e):
                    # Exponential backoff with jitter for quota errors
                    base_wait = 2 ** attempt          # 2, 4, 8 seconds
                    jitter = random.uniform(0, 1.5)
                    wait = base_wait + jitter
                    logger.warning(
                        f"[{stage}] Quota hit on {endpoint} attempt {attempt}. "
                        f"Waiting {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)

                    # After all retries on this endpoint, try fallback
                    if attempt == MAX_RETRIES:
                        logger.warning(f"[{stage}] Exhausted retries on {endpoint}, trying fallback")
                        break  # break inner loop → try next endpoint

                else:
                    # Non-quota error — don't retry endlessly
                    logger.error(f"[{stage}] Non-quota error: {e}")
                    raise

    raise RuntimeError(
        f"[{stage}] All endpoints and retries exhausted. "
        f"Total attempts: {total_attempts}"
    )


async def stream_llm(stage: str, contents: list):
    """Async generator yielding raw text chunks. Used for final streaming stage."""
    endpoints = MODEL_ENDPOINTS[stage]

    for endpoint in endpoints:
        client, model_id = _get_client_and_model(stage, endpoint)
        try:
            stream = await client.aio.models.generate_content_stream(
                model=model_id,
                contents=contents
            )
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text
            return  # success — stop trying endpoints

        except Exception as e:
            if _is_quota_error(e):
                logger.warning(f"[stream_llm/{stage}] Quota on {endpoint}, trying fallback")
                continue
            raise

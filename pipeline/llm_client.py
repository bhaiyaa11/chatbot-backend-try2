import json, time, asyncio, logging, random
from google import genai
from config import MODEL_ENDPOINTS, MAX_RETRIES, get_genai_client
from pipeline.cache import cache
from dotenv import load_dotenv
import base64



load_dotenv()

from anthropic import AsyncAnthropic
import os

anthropic_client = AsyncAnthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)


logger = logging.getLogger(__name__)


# --------------------------------------------------
# Convert Google GenAI Parts → Anthropic content blocks
# --------------------------------------------------

def _part_to_anthropic_block(part) -> dict | None:
    """
    Converts a google.genai.types.Part (image/video, built via
    Part.from_bytes) into an Anthropic-compatible content block.
    Returns None for unsupported types (e.g. video — Claude's
    Messages API doesn't accept video input).
    """
    try:
        inline = getattr(part, "inline_data", None)
        if inline is None or inline.data is None:
            return None

        mime = (inline.mime_type or "").lower()
        data = inline.data
        b64 = (
            base64.b64encode(data).decode("utf-8")
            if isinstance(data, (bytes, bytearray))
            else data
        )

        if mime.startswith("image/"):
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            }

        if mime == "application/pdf":
            return {
                "type": "document",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            }

        logger.warning(f"[Anthropic] Unsupported media type skipped: {mime}")
        return None

    except Exception as e:
        logger.error(f"[Anthropic] Failed to convert file part: {e}")
        return None


def _build_anthropic_content(contents: list) -> list:
    """
    Splits a mixed `contents` list (strings + types.Part objects) into
    Anthropic content blocks. Media blocks are placed before the text
    block, per Anthropic's recommendation for image-referencing prompts.
    """
    media_blocks = []
    text_chunks = []

    for item in contents:
        if isinstance(item, str):
            if item.strip():
                text_chunks.append(item)
        else:
            block = _part_to_anthropic_block(item)
            if block:
                media_blocks.append(block)

    blocks = list(media_blocks)
    if text_chunks:
        blocks.append({"type": "text", "text": "\n\n".join(text_chunks)})

    return blocks


# --------------------------------------------------
# Location map — only CRITIC uses global
# --------------------------------------------------
_STAGE_LOCATIONS = {
    "VOICE_OVER": "us",
    "VISUALS":    "us",
    # "VOICE_OVER": "us-central1",
    # "VISUALS":    "us-central1",
    "CRITIC":     "global",
}

def _get_client_and_model(stage: str, endpoint: str):
    location = _STAGE_LOCATIONS.get(stage, "us")
    client = get_genai_client(location=location)
    model_id = endpoint.strip()
    return client, model_id


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


async def generate_text(stage: str, contents: list) -> str:
    """
    Non-JSON LLM call.
    Uses Anthropic directly for CRITIC.
    Uses Google GenAI / Vertex AI for all other stages.
    """

    # ── Anthropic CRITIC ──────────────────────────────────────────
    # if stage == "CRITIC":
    #     try:
    #         response = await anthropic_client.messages.create(
    #             model="claude-sonnet-4-6",
    #             max_tokens=4000,
    #             messages=[
    #                 {
    #                     "role": "user",
    #                     "content": "\n\n".join(str(x) for x in contents),
    #                 }
    #             ],
    #         )
    if stage == "CRITIC":
        try:
            content_blocks = _build_anthropic_content(contents)

            if not content_blocks:
                raise RuntimeError("No content to send to CRITIC")

            response = await anthropic_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                messages=[
                    {
                        "role": "user",
                        "content": content_blocks,
                    }
                ],
            )

            return "".join(
                block.text
                for block in response.content
                if block.type == "text"
            ).strip()

        except Exception as e:
            logger.error(f"[generate_text/CRITIC] Anthropic error: {e}")
            raise RuntimeError(f"CRITIC generation failed: {e}")

    # ── Google models for all other stages ─────────────────────────
    endpoints = MODEL_ENDPOINTS[stage]

    for endpoint in endpoints:
        client, model_id = _get_client_and_model(stage, endpoint)

        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
            )
            return (response.text or "").strip()

        except Exception as e:
            if _is_quota_error(e):
                logger.warning(
                    f"[generate_text/{stage}] Quota on {endpoint}, trying fallback"
                )
                continue
            raise

    raise RuntimeError(f"[{stage}] Failed to generate text (non-JSON mode)")



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
            # async for chunk in stream:
            #     if chunk.text:
            #         yield chunk.text
            # return  # success — stop trying endpoints
            if stage == "CRITIC":

                stream = await anthropic_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4000,
                    stream=True,
                    messages=[
                        {
                            "role": "user",
                            # "content": "\n\n".join(str(x) for x in contents)
                            "content": _build_anthropic_content(contents)
                        }
                    ]
                )

                async for event in stream:
                    if event.type == "content_block_delta":
                        yield event.delta.text

                return

        except Exception as e:
            if _is_quota_error(e):
                logger.warning(f"[stream_llm/{stage}] Quota on {endpoint}, trying fallback")
                continue
            raise

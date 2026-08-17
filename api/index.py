from anthropic import AsyncAnthropic
import os
import uuid, json, logging, asyncio
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, APIRouter, Query, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
# from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import STAGE_LOCATIONS
from ingest.file_parser import parse_files
from pipeline.orchestrator import run_pipeline, run_conversational_pipeline
from supabase import create_client
from dotenv import load_dotenv
# from pipeline.fine_tune import export_training_jsonl, trigger_fine_tune_job
import base64
from pipeline.stages.niche_research import NicheResearchStage
from memory.log_store import get_logs
from memory.conversation_manager import ConversationManager
from memory.context_assembler import ContextAssembler
from memory.summarizer import ConversationSummarizer
from memory.vector_memory import VectorMemory
from pydantic import BaseModel
from pipeline.creative_review_pipeline import run_creative_review
from pipeline.creative_review_pipeline import run_generate_script_pipeline
from tts.tts import generate_cinematic_voiceover, stream_audio_chunks
from api.auth import get_current_user
from canvas.canvas_routes import (
    router as canvas_router,
    public_router as public_canvas_router,
    limiter as canvas_limiter,
)
from notifications_routes import router as notifications_router
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import traceback
from tts.tts import (
    generate_narration_script,
    generate_script_metadata,
    build_voice_settings,
    VOICE_AGENTS,
    eleven_client,
)
from urllib.parse import unquote

logger = logging.getLogger(__name__)

WORKING_MODEL          = "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview"
VERTEX_SEARCH_PROJECT  = "poc-script-genai"
VERTEX_SEARCH_LOCATION = "global"
VERTEX_SEARCH_APP_ID   = "script-research_1773405109220"

# load_dotenv()
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# supabase_client = create_client(
#     os.getenv("SUPABASE_URL"),
#     os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    
# )
# from supabase import create_client as create_auth_client
# supabase_auth_client = create_auth_client(
#     os.getenv("SUPABASE_URL"),
#     os.getenv("SUPABASE_SERVICE_ROLE_KEY"),  # must be the service_role key, not anon
# )


from db.client import supabase

supabase_client = supabase

# ---------------------------------------------------------------------------
# Memory layer initialization
# ---------------------------------------------------------------------------
conversation_manager = ConversationManager(supabase)
summarizer           = ConversationSummarizer(supabase)
vector_memory        = VectorMemory(supabase)
context_assembler    = ContextAssembler(
    conversation_manager=conversation_manager,
    summarizer=summarizer,
    vector_memory=vector_memory,
)

anthropic_client = AsyncAnthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

class VoiceRequest(BaseModel):
    script:     str
    voice_type: str

app = FastAPI()

# Comma-separated list in production, e.g.:
#   CORS_ALLOWED_ORIGINS=https://app.yourdomain.com,https://staging.yourdomain.com
# Falls back to local dev origins if unset, so nothing breaks locally.
_env_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
_ALLOWED_ORIGINS = (
    [o.strip() for o in _env_origins.split(",") if o.strip()]
    if _env_origins
    else ["http://localhost:5173", "http://localhost:3000"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=r"https://chatbot-[a-zA-Z0-9\-]+\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (slowapi) for the unauthenticated public-link endpoints
# defined in canvas_routes.py. Required for the @limiter.limit(...)
# decorators there to work at all — without app.state.limiter set,
# those decorated routes throw on every request.
app.state.limiter = canvas_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.on_event("startup")
async def startup():
    logger.info("✅ Server started — all state is in Supabase (stateless API)")


@app.get("/health")
async def health():
    return {"status": "ok"}

# Canvas API
app.include_router(canvas_router)
app.include_router(public_canvas_router)
app.include_router(notifications_router)


# ---------------------------------------------------------------------------
# /creative-review
# ---------------------------------------------------------------------------
@app.post("/creative-review")
async def creative_review_endpoint(
    prompt:           str   = Form(""),
    client:           str   = Form(""),
    business_unit:    str   = Form(""),
    video_type:       str   = Form(""),
    video_tone:       str   = Form(""),
    styles:           str   =Form(""),
    industries:       str   = Form(""),
    serviceLines:     str   = Form(""),
    
    duration:         str   = Form(""),
    creativity_ratio: float = Form(0.5),
    conversation_id:  str   = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    try:
        metadata = {
            "client":        client,
            "business_unit": business_unit,
            "styles":        styles,
            "industries":    industries,
            "serviceLines":  serviceLines,
            "video_type":    video_type,
            "video_tone":    video_tone,
            "duration":      duration,
        }
        metadata = {k: v for k, v in metadata.items() if v}

        file_parts = await parse_files(files or [], stage="NICHE_RESEARCH")

        result = await run_creative_review(
            prompt=prompt,
            metadata=metadata,
            creativity_ratio=creativity_ratio,
            file_parts=file_parts,
        )

        return JSONResponse({
            "review_id":            str(uuid.uuid4()),
            "retrievals":           [],
            "essences":             result["essences"],
            "interpretations":      result["interpretations"],
            "creative_summary":     result["creative_summary"],
            "semantic_inspiration": result["semantic_inspiration"],
        })

    except Exception as e:
        logger.error(f"[/creative-review] Error: {e}")
        traceback.print_exc()
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500,
        )



# ---------------------------------------------------------------------------
# /chat — conversational script generation
# user_id now comes from the JWT (Authorization header) instead of Form field.
# The Form("anonymous") fallback is kept for backward compat during migration.
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat(
    prompt:           str   = Form(""),
    debug:            bool  = Form(False),
    conversation_id:  str   = Form(""),
    client:           str   = Form(""),
    business_unit:    str   = Form(""),
    styles:           str   = Form(""),
    industries:       str   = Form(""),
    serviceLines:     str   = Form(""),
    video_type:       str   = Form(""),
    video_tone:       str   = Form(""),
    duration:         str   = Form(""),
    research_id:      str   = Form(""),
    research_brief:   str   = Form(""),
    creativity_ratio: float = Form(0.5),
    approved_essences:        str = Form("[]"),
    approved_interpretations: str = Form("[]"),
    creative_summary:         str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
    # JWT-derived user_id — falls back to "anonymous" if no token sent
    user_id: str = Depends(get_current_user),
):
    trace_id      = str(uuid.uuid4())[:8]
    pipeline_trace = []

    # Resolve research brief
    parsed_research = None
    if research_id:
        parsed_research = await conversation_manager.get_research_brief(research_id)
        if parsed_research:
            logger.info(f"[{trace_id}] Loaded research brief from DB (id={research_id})")
    if not parsed_research and research_brief:
        try:
            parsed_research = json.loads(research_brief)
            logger.info(f"[{trace_id}] Parsed research_brief from JSON string")
        except Exception:
            logger.warning(f"[{trace_id}] Could not parse research_brief JSON")

    parsed_approved_essences = json.loads(approved_essences)
    logger.info(f"Approved essences received: {len(parsed_approved_essences)}")

    parsed_approved_interpretations = json.loads(approved_interpretations)
    logger.info(f"Approved interpretations received: {len(parsed_approved_interpretations)}")

    metadata = {k: v for k, v in {
        "client":        client,
        "business_unit": business_unit,
        "styles":        styles,
        "industries":    industries,
        "serviceLines":  serviceLines,
        "video_type":    video_type,
        "video_tone":    video_tone,
        "duration":      duration,
    }.items() if v}

    conversation = await conversation_manager.get_or_create_conversation(
        conversation_id=conversation_id or None,
        metadata=metadata,
        user_id=user_id,
    )
    conv_id = conversation.id
    logger.info(f"[{trace_id}] Conversation: {conv_id} (msgs={conversation.message_count})")

    user_message_metadata = {**metadata}
    if research_id:
        user_message_metadata["research_id"] = research_id

    user_msg = await conversation_manager.save_message(
        conversation_id=conv_id,
        role="user",
        content=prompt,
        message_type="text",
        metadata=user_message_metadata,
        user_id=user_id,
    )
# Set title only once when conversation is new
    if not conversation.title:
        await conversation_manager.update_conversation_title(
            conv_id,
            prompt[:60].strip()
        )
        
    context = await context_assembler.assemble(
        conversation_id=conv_id,
        user_id=user_id,
        current_prompt=prompt,
    )

    async def stream():
        full_output = []
        yield f"conversation_id:{conv_id}\n"
        try:
            file_parts = await parse_files(files or [], stage="VOICE_OVER")
            preferences = {"creativity_ratio": creativity_ratio}

            async for chunk in run_conversational_pipeline(
                prompt=prompt,
                context=context,
                file_parts=file_parts,
                trace=pipeline_trace,
                client=client,
                business_unit=business_unit,
                styles=styles,
                industries=industries,
                serviceLines=serviceLines,
                video_type=video_type,
                video_tone=video_tone,
                duration=duration,
                research_brief=parsed_research,
                preferences=preferences,
                approved_essences=parsed_approved_essences,
                approved_interpretations=parsed_approved_interpretations,
                creative_summary=creative_summary,
            ):
                if chunk.startswith("result:"):
                    full_output.append(chunk[7:].strip())
                yield chunk

            if debug:
                yield f"debug:{json.dumps({'id': trace_id, 'trace': pipeline_trace})}\n"

            if full_output:
                combined_output = "\n".join(full_output).strip()
                if combined_output:
                    msg_type = "script_edit" if context.last_script else "script_generation"
                    assistant_msg = await conversation_manager.save_message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=combined_output,
                        message_type=msg_type,
                        metadata={"trace_id": trace_id},
                        user_id=user_id,
                    )

            

        except Exception as e:
            logger.error(f"[{trace_id}] Unhandled stream error: {e}")
            yield f"error:Server error — {str(e)}\n"

    return StreamingResponse(stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# /edit — lightweight inline text editing
# ---------------------------------------------------------------------------
_EDIT_SYSTEM_PROMPT = """You are a professional script editor.
The user will give you an instruction and a piece of selected text.
Apply the instruction to the selected text only.
Return ONLY the edited text — no explanations, no preamble, no quotes.
Preserve the original tone and style unless the instruction says otherwise."""


@app.post("/edit")
async def edit(
    instruction:   str = Form(...),
    selected_text: str = Form(...),
):
    try:
        from config import get_genai_client
        genai_client = get_genai_client(location=STAGE_LOCATIONS.get("CRITIC", "global"))
        prompt = f"{_EDIT_SYSTEM_PROMPT}\n\nInstruction: {instruction}\n\nText:\n{selected_text}"
        response = await genai_client.aio.models.generate_content(
            model=WORKING_MODEL,
            contents=prompt,
        )
        return JSONResponse({"result": response.text.strip()})
    except Exception as e:
        logger.error(f"[/edit] Error: {e}")
        return JSONResponse({"result": None, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# /feedback
# ---------------------------------------------------------------------------
@app.post("/feedback")
async def feedback(
    prompt: str = Form(""),
    output: str = Form(""),
    rating: int = Form(...),
):
    print("FEEDBACK RECEIVED", prompt[:20], rating)
    response = supabase_client.table("training_data").insert({
        "prompt": prompt,
        "output": output,
        "rating": rating,
    }).execute()
    print("SUPABASE RESPONSE:", response)
    return {"status": "saved"}


# ---------------------------------------------------------------------------
# /research — niche research (stores brief in DB)
# ---------------------------------------------------------------------------
@app.post("/research")
async def run_research(
    client:           str   = Form(""),
    business_unit:    str   = Form(""),
    styles:           str   = Form(""),
    industries:       str   = Form(""),
    serviceLines:     str   = Form(""),
    video_type:       str   = Form(""),
    video_tone:       str   = Form(""),
    duration:         str   = Form(""),
    prompt:           str   = Form(""),
    creativity_ratio: float = Form(0.5),
    files: Optional[List[UploadFile]] = File(None),
):
    metadata = {
        "client":        client,
        "business_unit": business_unit,
        "styles": styles,
        "industries": industries,
        "serviceLines": serviceLines,
        "video_type":    video_type,
        "video_tone":    video_tone,
        "duration":      duration,
        "prompt":        prompt,
    }

    file_parts = await parse_files(files or [], stage="NICHE_RESEARCH")

    stage  = NicheResearchStage()
    result = await stage.run(metadata=metadata, file_parts=file_parts)

    research_id = str(uuid.uuid4())[:12]

    if result.success and result.data:
        await conversation_manager.save_research_brief(
            short_id=research_id,
            data=result.data,
            metadata=metadata,
        )

    return {
        "success":     result.success,
        "research":    result.data,
        "research_id": research_id,
        "error":       result.error,
    }


@app.get("/research/{research_id}")
async def get_research_brief(research_id: str):
    try:
        data = await conversation_manager.get_research_brief(research_id)
        if data is None:
            return JSONResponse({"error": "Research brief not found"}, status_code=404)
        return {"success": True, "research": data, "research_id": research_id}
    except Exception as e:
        logger.error(f"[/research/{research_id}] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# /messages — paginated message retrieval
# ---------------------------------------------------------------------------
@app.get("/messages")
async def get_messages(
    chat_id: str = Query(None, description="Chat ID (backward compat)"),
    conversation_id: str = Query(None, description="Conversation ID"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    # limit: int = Query(2000, ge=1, le=2000, description="Messages per page"),
    limit: int | None = Query(None, ge=1),
):
    try:
        target_id = conversation_id or chat_id
        if not target_id:
            return JSONResponse(
                {"messages": [], "error": "conversation_id or chat_id required"},
                status_code=400,
            )

        offset = (page - 1) * limit
        filter_column = "conversation_id" if conversation_id else "chat_id"

        response = (
            supabase_client
            .table("messages")
            .select("id, chat_id, conversation_id, role, content, message_type, metadata, created_at")
            .eq(filter_column, target_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )

        messages = response.data or []
        has_more = len(messages) == limit
        messages.reverse()

        return {
            "messages": messages,
            "page": page,
            "limit": limit,
            "has_more": has_more,
        }
    except Exception as e:
        logger.error(f"[/messages] Error fetching messages: {e}")
        return JSONResponse(
            {"messages": [], "page": page, "limit": limit, "has_more": False, "error": str(e)},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# /conversations — CRUD (all filtered by authenticated user_id)
# ---------------------------------------------------------------------------
@app.get("/conversations")
async def list_conversations(
    limit:   int = Query(20, ge=1, le=100),
    offset:  int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
):
    conversations = await conversation_manager.list_conversations(
        limit=limit,
        offset=offset,
        user_id=user_id,
    )
    return {
        "conversations": [
            {
                "id":            c.id,
                "title":         c.title,
                "metadata":      c.metadata,
                "created_at":    c.created_at,
                "updated_at":    c.updated_at,
                "message_count": c.message_count,
            }
            for c in conversations
        ]
    }


@app.get("/conversations/{conv_id}")
async def get_conversation(
    conv_id: str,
    user_id: str = Depends(get_current_user),
):
    # conv = await conversation_manager.get_conversation(conv_id)
    # if not conv:
    #     return JSONResponse({"error": "Conversation not found"}, status_code=404)

    # # Ownership check — prevent users from reading each other's conversations
    # if hasattr(conv, "user_id") and conv.user_id and conv.user_id != user_id:
    #     return JSONResponse({"error": "Forbidden"}, status_code=403)
    conv = await conversation_manager.get_conversation(
        conv_id,
        user_id,
    )

    if not conv:
        return JSONResponse(
            {"error": "Conversation not found"},
            status_code=404,
        )

    summary_data = await summarizer.get_latest_summary(conv_id)

    return {
        "id":            conv.id,
        "title":         conv.title,
        "metadata":      conv.metadata,
        "created_at":    conv.created_at,
        "updated_at":    conv.updated_at,
        "message_count": conv.message_count,
        "summary":       summary_data.get("summary") if summary_data else None,
    }


@app.delete("/conversations/{conv_id}")
async def archive_conversation(
    conv_id: str,
    user_id: str = Depends(get_current_user),
):
    # Ownership check before archiving
    # conv = await conversation_manager.get_conversation(conv_id)
    # if conv and hasattr(conv, "user_id") and conv.user_id and conv.user_id != user_id:
    #     return JSONResponse({"error": "Forbidden"}, status_code=403)
    success = await conversation_manager.archive_conversation(
    conv_id,
    user_id,
)

    success = await conversation_manager.archive_conversation(conv_id)
    if success:
        return {"status": "archived"}
    return JSONResponse({"error": "Failed to archive"}, status_code=500)


# ---------------------------------------------------------------------------
# /logs — context debug
# ---------------------------------------------------------------------------
@app.get("/logs")
def get_context_logs():
    return get_logs()



# ---------------------------------------------------------------------------
# /enhance — prompt enhancer
# ---------------------------------------------------------------------------
@app.post("/enhance")
async def enhance_prompt(
    prompt:        str = Form(...),
    client:        str = Form(""),
    video_type:    str = Form(""),
    video_tone:    str = Form(""),
    duration:      str = Form(""),
    styles:        str = Form(""),
    industries:    str = Form(""),
    serviceLines:  str = Form(""),
):
    try:
        system_prompt = """
You are a world-class creative strategist and prompt engineer.

Your task is to transform a rough user request into a high-quality
video script generation brief.

Rules:
- Preserve the user's intent.
- Never change the topic.
- Expand vague requests into clearer creative directions.
- Weave in the provided campaign parameters (client, video type, tone,
  duration, styles, industries, service lines) naturally — do not just
  list them, integrate them into the creative direction.
- Make the request more specific, cinematic and actionable.
- Improve clarity and structure.
- STRICT LENGTH LIMIT: output must be 4-5 lines maximum. Do not exceed this.
- Return ONLY the improved prompt.
- Do not explain your reasoning.
- Do not use markdown.
"""

        context_lines = []
        if client:        context_lines.append(f"Client: {client}")
        if video_type:    context_lines.append(f"Video Type: {video_type}")
        if video_tone:    context_lines.append(f"Tone: {video_tone}")
        if duration:      context_lines.append(f"Duration: {duration}")
        if styles:        context_lines.append(f"Styles: {styles}")
        if industries:    context_lines.append(f"Industries: {industries}")
        if serviceLines:  context_lines.append(f"Service Lines: {serviceLines}")

        context_block = "\n".join(context_lines)

        user_message = (
            f"CAMPAIGN PARAMETERS:\n{context_block}\n\n"
            f"USER REQUEST:\n{prompt}"
            if context_block
            else prompt
        )

        response = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return {"success": True, "enhanced": response.content[0].text.strip()}

    except Exception as e:
        logger.error(f"[/enhance] {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# /generate-voice — TTS
# ---------------------------------------------------------------------------


from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse
import uuid
import os

OUTPUT_DIR = "generated_audio"
os.makedirs(OUTPUT_DIR, exist_ok=True)



def alignment_to_words(alignment):
    """
    Convert ElevenLabs character-level alignment
    into word-level timing data.
    """

    if not alignment:
        return []

    characters = alignment.characters
    starts = alignment.character_start_times_seconds
    ends = alignment.character_end_times_seconds

    words = []

    current_word = []
    word_start = None
    word_end = None

    for char, start, end in zip(characters, starts, ends):

        # Whitespace means the current word has ended
        if char.isspace():

            if current_word:
                words.append({
                    "word": "".join(current_word),
                    "start": word_start,
                    "end": word_end,
                })

                current_word = []
                word_start = None
                word_end = None

            continue

        # Start a new word
        if not current_word:
            word_start = start

        current_word.append(char)
        word_end = end

    # Handle final word
    if current_word:
        words.append({
            "word": "".join(current_word),
            "start": word_start,
            "end": word_end,
        })

    return words


@app.post("/generate-voice")
async def generate_voice(data: VoiceRequest):

    file_id = str(uuid.uuid4())
    filename = f"{file_id}.mp3"

    output_path = os.path.join(
        OUTPUT_DIR,
        filename
    )

    narration_script = await generate_narration_script(
        data.script
    )

    metadata = await generate_script_metadata(
        narration_script
    )

    voice_agent = VOICE_AGENTS[data.voice_type]

    voice_settings = build_voice_settings(
        metadata
    )

    response = await eleven_client.text_to_speech.convert_with_timestamps(
        voice_id=voice_agent["voice_id"],
        model_id="eleven_flash_v2_5",
        text=narration_script,
        output_format="mp3_44100_128",
        voice_settings=voice_settings,
    )

    audio_bytes = base64.b64decode(response.audio_base_64)
    alignment = response.alignment

    word_timings = alignment_to_words(alignment)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return {
        "success": True,
        "audio_url": f"/audio/{filename}",
        "alignment_received": alignment is not None,
        "word_timings": word_timings,
    }

@app.get("/audio/{filename}")
async def get_audio(filename: str):

    path = os.path.join(
        OUTPUT_DIR,
        filename
    )

    return FileResponse(
        path,
        media_type="audio/mpeg"
    )


# ---------------------------------------------------------------------------
# /fact-check
# ---------------------------------------------------------------------------
_FACT_CHECK_SYSTEM_PROMPT = """You are a professional fact-checker reviewing a video script.
Identify every factual claim — statistics, dates, named entities, product/company facts, scientific assertions, historical events.
For each claim, assess: accurate, inaccurate, unverifiable, or misleading.

Return ONLY valid JSON (no markdown, no backticks):
{
  "summary": "One sentence overall verdict.",
  "score": 85,
  "claims": [
    {
      "claim": "The exact quoted text from the script",
      "verdict": "accurate | inaccurate | unverifiable | misleading",
      "explanation": "1-2 sentences. If inaccurate, state the correct fact.",
      "source_hint": "What to search to verify this (optional)"
    }
  ]
}
Only include claims with real factual content. Ignore metaphors, opinions, and narrative sentences."""


class FactCheckRequest(BaseModel):
    script: str

@app.post("/fact-check")
async def fact_check(data: FactCheckRequest):
    try:
        script = data.script.strip()
        if not script:
            return JSONResponse({"error": "No script provided"}, status_code=400)

        response = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10000,
            system=_FACT_CHECK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Fact-check this script:\n\n{script}"}],
        )

        raw = response.content[0].text.strip()

        import re
        clean = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean).strip()

        parsed = json.loads(clean)
        return JSONResponse(parsed)

    except json.JSONDecodeError as e:
        logger.error(f"[/fact-check] JSON parse error: {e}\nRaw: {raw}")
        return JSONResponse({"error": "Model returned invalid JSON"}, status_code=500)
    except Exception as e:
        logger.error(f"[/fact-check] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
    


from faster_whisper import WhisperModel

whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
import tempfile

# ---------------------------------------------------------------------------
# /transcribe — local Whisper STT for voice-to-prompt input
# ---------------------------------------------------------------------------
@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    try:
        contents = await audio.read()
        if not contents:
            return JSONResponse({"text": "", "error": "Empty audio file"}, status_code=400)

        suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        try:
            segments, info = whisper_model.transcribe(tmp_path, language="en")
            text = " ".join(seg.text.strip() for seg in segments)
            return {"text": text.strip()}
        finally:
            os.remove(tmp_path)

    except Exception as e:
        logger.error(f"[/transcribe] Error: {e}")
        traceback.print_exc()
        return JSONResponse({"text": "", "error": str(e)}, status_code=500)



# 
# 
#  // Visual generator 
# 
# 

from pipeline.stages.visuals_generator import (
    run_storyboard_job,
    run_scene_edit_job,
    get_job,
    VIDEO_TYPE_LABELS,
)


# ============================================================
# STORYBOARD CONFIG
# ============================================================

STORYBOARD_OUTPUT_DIR = "generated_storyboards"
os.makedirs(STORYBOARD_OUTPUT_DIR, exist_ok=True)


# ============================================================
# REQUEST MODELS
# ============================================================

class StoryboardRequest(BaseModel):
    script: str
    video_type: str
    quality: str = "quality"


class SceneEditRequest(BaseModel):
    prompt: str
    mode: str = "edit"          # "edit" | "regenerate"
    video_type: str = ""
    quality: str = "quality"



@app.post("/generate-storyboard")
async def start_storyboard(
    data: StoryboardRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):
    # --------------------------------------------------------
    # Validate video type
    # --------------------------------------------------------

    if data.video_type not in VIDEO_TYPE_LABELS:
        return JSONResponse(
            {
                "error": (
                    "Invalid video_type. "
                    f"Must be one of: {list(VIDEO_TYPE_LABELS)}"
                )
            },
            status_code=400,
        )

    # --------------------------------------------------------
    # Validate script
    # --------------------------------------------------------

    if not data.script.strip():
        return JSONResponse(
            {"error": "No script provided"},
            status_code=400,
        )

    # --------------------------------------------------------
    # Create job ID
    # --------------------------------------------------------

    job_id = str(uuid.uuid4())[:12]

    # --------------------------------------------------------
    # Persist job in Supabase
    # --------------------------------------------------------

    try:
        supabase_client.table("storyboard_jobs").insert(
            {
                "job_id": job_id,
                "user_id": user_id,
                "status": "pending",
                "script": data.script,
                "video_type": data.video_type,
                "quality": data.quality,
            }
        ).execute()

    except Exception as e:
        logger.error(
            f"[generate-storyboard] Failed to create job: {e}"
        )

        return JSONResponse(
            {"error": "Failed to create storyboard job"},
            status_code=500,
        )

    # --------------------------------------------------------
    # Start generation
    # --------------------------------------------------------

    background_tasks.add_task(
        run_storyboard_job,
        job_id,
        data.script,
        data.video_type,
        data.quality,
        STORYBOARD_OUTPUT_DIR,
        user_id,
    )

    return {
        "job_id": job_id,
        "status": "pending",
    }



# ============================================================
# EDIT / REGENERATE INDIVIDUAL SCENE
# ============================================================

@app.post("/generate-storyboard/{job_id}/scenes/{scene_number}")
async def edit_storyboard_scene(
    job_id: str,
    scene_number: int,
    data: SceneEditRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):

    scene_response = (
        supabase_client
        .table("storyboard_scenes")
        .select("scene_number,status")
        .eq("job_id", job_id)
        .eq("user_id", user_id)
        .eq("scene_number", scene_number)
        .maybe_single()
        .execute()
    )

    scene = scene_response.data

    if not scene:
        return JSONResponse(
            {"error": "Scene not found"},
            status_code=404,
        )

    if scene.get("status") == "generating":
        return JSONResponse(
            {"error": "Scene is already regenerating"},
            status_code=409,
        )

    # --------------------------------------------------------
    # Validate mode
    # --------------------------------------------------------

    if data.mode not in ("edit", "regenerate"):
        return JSONResponse(
            {
                "error": (
                    "mode must be 'edit' or 'regenerate'"
                )
            },
            status_code=400,
        )

    # --------------------------------------------------------
    # Validate prompt
    # --------------------------------------------------------

    if not data.prompt.strip():
        return JSONResponse(
            {"error": "prompt is required"},
            status_code=400,
        )


    # --------------------------------------------------------
    # Run edit/regeneration in background
    # --------------------------------------------------------

    background_tasks.add_task(
        run_scene_edit_job,
        job_id,
        scene_number,
        data.prompt,
        data.mode,
        data.video_type,
        data.quality,
        STORYBOARD_OUTPUT_DIR,
        user_id,
    )

    return {
        "status": "generating",
        "scene_number": scene_number,
    }


# ============================================================
# GET STORYBOARD STATUS
# ============================================================


@app.get("/generate-storyboard/{job_id}")
async def get_storyboard_status(
    job_id: str,
    user_id: str = Depends(get_current_user),
):
    try:
        # ----------------------------------------------------
        # Get storyboard job
        # ----------------------------------------------------

        job_response = (
            supabase_client
            .table("storyboard_jobs")
            .select("*")
            .eq("job_id", job_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )

        job = job_response.data

        if not job:
            return JSONResponse(
                {"error": "Storyboard not found"},
                status_code=404,
            )

        # ----------------------------------------------------
        # Get scenes
        # ----------------------------------------------------

        scenes_response = (
            supabase_client
            .table("storyboard_scenes")
            .select("*")
            .eq("job_id", job_id)
            .eq("user_id", user_id)
            .order("scene_number")
            .execute()
        )

        scenes = scenes_response.data or []

        images = []

        for scene in scenes:
            image_data = scene.get("image_data")

            if not image_data:
                continue

            # PostgreSQL stores the image as Base64 text.
            # Turn it into a browser-readable data URL.
            mime_type = scene.get(
                "mime_type",
                "image/png",
            )

            image_url = (
                f"data:{mime_type};base64,{image_data}"
            )

            images.append(
                {
                    "url": image_url,
                    "scene_number": scene["scene_number"],
                    "caption": scene.get(
                        "caption",
                        "",
                    ),
                    "scene_status": scene.get(
                        "status",
                        "idle",
                    ),
                }
            )

        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "total_scenes": job["total_scenes"],
            "completed_count": len(images),
            "error": job.get("error"),
            "images": images,
        }

    except Exception as e:
        logger.error(
            f"[generate-storyboard/{job_id}] "
            f"Failed to load storyboard: {e}"
        )

        return JSONResponse(
            {"error": "Failed to load storyboard"},
            status_code=500,
        )


# ============================================================
# SERVE STORYBOARD IMAGE
# ============================================================

@app.get("/storyboard-images/{filename}")
async def get_storyboard_image(filename: str):

    path = os.path.join(
        STORYBOARD_OUTPUT_DIR,
        filename,
    )

    if not os.path.exists(path):
        return JSONResponse(
            {"error": "Not found"},
            status_code=404,
        )

    return FileResponse(
        path,
        media_type="image/png",
    )







# uvicorn api.index:app --reload
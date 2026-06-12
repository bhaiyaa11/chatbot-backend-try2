from anthropic import AsyncAnthropic
import os
import uuid, json, logging, asyncio
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from config import STAGE_LOCATIONS
from ingest.file_parser import parse_files
from pipeline.orchestrator import run_pipeline, run_conversational_pipeline
from supabase import create_client
from dotenv import load_dotenv
from pipeline.fine_tune import export_training_jsonl, trigger_fine_tune_job
from pipeline.stages.niche_research import NicheResearchStage
from memory.log_store import get_logs
from memory.conversation_manager import ConversationManager
from memory.context_assembler import ContextAssembler
from memory.summarizer import ConversationSummarizer
from memory.vector_memory import VectorMemory
from pydantic import BaseModel
# from pipeline.creative_review import run_creative_review
from pipeline.creative_review_pipeline import run_creative_review
from pipeline.creative_review_pipeline import run_generate_script_pipeline
from tts.tts import generate_cinematic_voiceover
import traceback

logger = logging.getLogger(__name__)

WORKING_MODEL          = "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview"
VERTEX_SEARCH_PROJECT  = "poc-script-genai"
VERTEX_SEARCH_LOCATION = "global"
VERTEX_SEARCH_APP_ID   = "script-research_1773405109220"

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase client (shared singleton)
# ---------------------------------------------------------------------------
supabase_client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY"),
)

# ---------------------------------------------------------------------------
# Memory layer initialization
# ---------------------------------------------------------------------------
conversation_manager = ConversationManager(supabase_client)
summarizer           = ConversationSummarizer(supabase_client)
vector_memory        = VectorMemory(supabase_client)
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

_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=r"https://chatbot-[a-zA-Z0-9\-]+\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    logger.info("✅ Server started — all state is in Supabase (stateless API)")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /creative-review
# Frontend sends FormData (not JSON), so all fields are Form() params.
# Returns a review_id plus creative guidance for the user to approve.
# ---------------------------------------------------------------------------
@app.post("/creative-review")
async def creative_review_endpoint(
    prompt:           str   = Form(""),
    client:           str   = Form(""),
    business_unit:    str   = Form(""),
    video_type:       str   = Form(""),
    video_tone:       str   = Form(""),
    duration:         str   = Form(""),
    creativity_ratio: float = Form(0.5),
    conversation_id:  str   = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    """
    Stage 1 of the two-step creative flow:
    Run niche research + creative interpretation, return a review payload
    the frontend presents to the user for approval before generating the script.
    """
    try:
        metadata = {
            "client":        client,
            "business_unit": business_unit,
            "video_type":    video_type,
            "video_tone":    video_tone,
            "duration":      duration,
        }
        # Strip empty values so downstream code doesn't see empty strings
        metadata = {k: v for k, v in metadata.items() if v}

        file_parts = await parse_files(files or [], stage="NICHE_RESEARCH")

        result = await run_creative_review(
            prompt=prompt,
            metadata=metadata,
            creativity_ratio=creativity_ratio,
            file_parts=file_parts,
        )

        # run_creative_review must return a dict with at least {"review_id": ...}
        # return JSONResponse(result)
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
# /chat — conversational script generation (original flow, unchanged)
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat(
    prompt:           str   = Form(""),
    debug:            bool  = Form(False),
    conversation_id:  str   = Form(""),
    client:           str   = Form(""),
    business_unit:    str   = Form(""),
    video_type:       str   = Form(""),
    video_tone:       str   = Form(""),
    duration:         str   = Form(""),
    research_id:      str   = Form(""),
    research_brief:   str   = Form(""),
    user_id:          str   = Form("anonymous"),
    creativity_ratio: float = Form(0.5),
    approved_essences:        str = Form("[]"),
    approved_interpretations: str = Form("[]"),
    creative_summary:         str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
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

    parsed_approved_essences        = json.loads(approved_essences)
    logger.info(
        f"Approved essences received: "
        f"{len(parsed_approved_essences)}"
    )

   
    parsed_approved_interpretations = json.loads(approved_interpretations)
    logger.info(
        f"Approved interpretations received: "
        f"{len(parsed_approved_interpretations)}"
    )

    metadata = {k: v for k, v in {
        "client":        client,
        "business_unit": business_unit,
        "video_type":    video_type,
        "video_tone":    video_tone,
        "duration":      duration,
    }.items() if v}

    conversation = await conversation_manager.get_or_create_conversation(
        conversation_id=conversation_id or None,
        metadata=metadata,
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

    context = await context_assembler.assemble(
        conversation_id=conv_id,
        current_prompt=prompt,
    )

    async def stream():
        full_output = []
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

            yield f"conversation_id:{conv_id}\n"

        except Exception as e:
            logger.error(f"[{trace_id}] Unhandled stream error: {e}")
            yield f"error:Server error — {str(e)}\n"

    return StreamingResponse(stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# Background memory tasks (shared by /chat and /generate-script)
# ---------------------------------------------------------------------------

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
    limit: int = Query(20, ge=1, le=100, description="Messages per page"),
):
    try:
        # Accept either conversation_id or chat_id
        target_id = conversation_id or chat_id
        if not target_id:
            return JSONResponse(
                {"messages": [], "error": "conversation_id or chat_id required"},
                status_code=400,
            )

        offset = (page - 1) * limit

        # Use conversation_id column if available, fall back to chat_id
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

        # Reverse so frontend receives oldest → newest order
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
# /conversations — CRUD
# ---------------------------------------------------------------------------
@app.get("/conversations")
async def list_conversations(
    limit:  int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    conversations = await conversation_manager.list_conversations(limit=limit, offset=offset)
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
async def get_conversation(conv_id: str):
    conv = await conversation_manager.get_conversation(conv_id)
    if not conv:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)

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
async def archive_conversation(conv_id: str):
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
async def enhance_prompt(prompt: str = Form(...)):
    try:
        system_prompt = """
You are a world-class creative strategist and prompt engineer.

Your task is to transform a rough user request into a high-quality
video script generation brief.

Rules:
- Preserve the user's intent.
- Never change the topic.
- Expand vague requests into clearer creative directions.
- Infer useful context when appropriate.
- Make the request more specific, cinematic and actionable.
- Improve clarity and structure.
- Keep the final prompt concise enough for production use.
- Return ONLY the improved prompt.
- Do not explain your reasoning.
- Do not use markdown.
"""
        response = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            temperature=0.7,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"success": True, "enhanced": response.content[0].text.strip()}

    except Exception as e:
        logger.error(f"[/enhance] {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# /generate-voice — TTS
# ---------------------------------------------------------------------------
@app.post("/generate-voice")
async def generate_voice(data: VoiceRequest):
    try:
        logger.info("VOICE REQUEST RECEIVED")
        result    = await generate_cinematic_voiceover(
            final_script=data.script,
            voice_type=data.voice_type,
        )
        audio_path = result["final_audio"]
        filename   = os.path.basename(audio_path)
        audio_url  = f"/audio/{filename}"
        return JSONResponse({"success": True, "audio_url": audio_url})

    except Exception as e:
        logger.error(f"[/generate-voice] {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/audio/{filename}")
async def stream_audio(filename: str):
    file_path = os.path.join("generated_audio", filename)
    if not os.path.exists(file_path):
        return JSONResponse({"success": False, "error": "Audio file not found"}, status_code=404)
    return FileResponse(
        path=file_path,
        media_type="audio/wav",
        headers={"Accept-Ranges": "none"},
    )


# ---------------------------------------------------------------------------
# /fact-check — factual accuracy check for script canvas
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

        # Strip markdown fences — model ignores "no backticks" instruction
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
    


# uvicorn api.index:app --reload
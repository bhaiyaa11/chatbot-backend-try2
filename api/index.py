import uuid, json, logging, asyncio
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from config import STAGE_LOCATIONS
from ingest.file_parser import parse_files
from pipeline.orchestrator import run_pipeline, run_conversational_pipeline
from supabase import create_client
from dotenv import load_dotenv
from pipeline.fine_tune import export_training_jsonl, trigger_fine_tune_job
import os
from pipeline.stages.niche_research import NicheResearchStage

# Memory layer — replaces all in-memory state
from memory.conversation_manager import ConversationManager
from memory.context_assembler import ContextAssembler
from memory.summarizer import ConversationSummarizer
from memory.vector_memory import VectorMemory

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
# All conversation state is now in Supabase — no in-memory caches.
# ---------------------------------------------------------------------------
conversation_manager = ConversationManager(supabase_client)
summarizer = ConversationSummarizer(supabase_client)
vector_memory = VectorMemory(supabase_client)
context_assembler = ContextAssembler(
    conversation_manager=conversation_manager,
    summarizer=summarizer,
    vector_memory=vector_memory,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
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
    """Quick endpoint to verify server is up."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /chat — MAIN ENDPOINT (redesigned for persistent conversation memory)
#
# Key changes from old version:
#   ❌ REMOVED: _sessions, detect_intent(), LAST_SCRIPT, _script_cache
#   ❌ REMOVED: keyword-based preference parsing
#   ❌ REMOVED: mode parameter (LLM infers from conversation context)
#   ✅ ADDED:   conversation_id for persistent memory
#   ✅ ADDED:   ContextAssembler for multi-tier memory
#   ✅ ADDED:   Background embedding generation + summarization
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat(
    prompt: str = Form(""),
    debug: bool = Form(False),
    conversation_id: str = Form(""),
    # ── metadata ──────────────────────────────────────────────────
    client:         str = Form(""),
    business_unit:  str = Form(""),
    video_type:     str = Form(""),
    video_tone:     str = Form(""),
    duration:       str = Form(""),
    research_id:    str = Form(""),
    research_brief: str = Form(""),
    user_id:        str = Form("anonymous"),
    files: Optional[List[UploadFile]] = File(None),
):
    trace_id = str(uuid.uuid4())[:8]
    pipeline_trace = []

    # ── 1. Resolve research brief from DB (replaces _research_cache) ──
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

    # ── 2. Get or create conversation ─────────────────────────────
    metadata = {
        "client": client,
        "business_unit": business_unit,
        "video_type": video_type,
        "video_tone": video_tone,
        "duration": duration,
    }
    # Filter out empty metadata values
    metadata = {k: v for k, v in metadata.items() if v}

    conversation = await conversation_manager.get_or_create_conversation(
        conversation_id=conversation_id or None,
        metadata=metadata,
    )
    conv_id = conversation.id
    logger.info(f"[{trace_id}] Conversation: {conv_id} (msgs={conversation.message_count})")

    # ── 3. Save the user message ──────────────────────────────────
    user_msg = await conversation_manager.save_message(
        conversation_id=conv_id,
        role="user",
        content=prompt,
        message_type="text",
        metadata=metadata,
        user_id=user_id,
    )

    # ── 4. Assemble context (short-term + long-term + vector) ─────
    context = await context_assembler.assemble(
        conversation_id=conv_id,
        current_prompt=prompt,
    )

    # ── 5. Stream response ────────────────────────────────────────
    async def stream():
        full_output = []
        try:
            file_parts = await parse_files(files or [], stage="VOICE_OVER")
            
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
            ):
                if chunk.startswith("result:"):
                    full_output.append(chunk[7:].strip())
                yield chunk

            if debug:
                yield f"debug:{json.dumps({'id': trace_id, 'trace': pipeline_trace})}\n"

            # ── 6. Save assistant response to DB ──────────────────
            if full_output:
                combined_output = "\n".join(full_output).strip()
                if combined_output:
                    # Determine message type based on whether this was an edit
                    msg_type = "script_edit" if context.last_script else "script_generation"
                    
                    assistant_msg = await conversation_manager.save_message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=combined_output,
                        message_type=msg_type,
                        metadata={"trace_id": trace_id},
                        user_id=user_id,
                    )

                    # ── 7. Background tasks (non-blocking) ────────
                    # Generate embeddings for both user prompt and assistant output
                    asyncio.create_task(
                        _background_memory_tasks(
                            conv_id=conv_id,
                            user_msg_id=user_msg.id,
                            assistant_msg_id=assistant_msg.id,
                            user_prompt=prompt,
                            assistant_output=combined_output,
                            msg_type=msg_type,
                        )
                    )

                    logger.info(
                        f"[{trace_id}] Saved {msg_type} to conversation {conv_id}"
                    )

            # ── Yield conversation_id so frontend can track it ────
            yield f"conversation_id:{conv_id}\n"

        except Exception as e:
            logger.error(f"[{trace_id}] Unhandled stream error: {e}")
            yield f"error:Server error — {str(e)}\n"

    return StreamingResponse(stream(), media_type="text/plain")


async def _background_memory_tasks(
    conv_id: str,
    user_msg_id: str,
    assistant_msg_id: str,
    user_prompt: str,
    assistant_output: str,
    msg_type: str,
):
    """
    Background tasks after each chat response:
    1. Generate and store embeddings for the user prompt
    2. Generate and store embeddings for the assistant output
    3. Check if summarization is needed and run it
    4. Auto-generate conversation title if first message
    
    All failures are caught and logged — never blocks the response.
    """
    # 1. Embed user prompt
    try:
        content_type = "edit_instruction" if msg_type == "script_edit" else "user_prompt"
        await vector_memory.store_embedding(
            message_id=user_msg_id,
            conversation_id=conv_id,
            content=user_prompt,
            content_type=content_type,
        )
    except Exception as e:
        logger.warning(f"[Background] User embedding failed: {e}")

    # 2. Embed assistant output
    try:
        await vector_memory.store_embedding(
            message_id=assistant_msg_id,
            conversation_id=conv_id,
            content=assistant_output,
            content_type="generated_script",
        )
    except Exception as e:
        logger.warning(f"[Background] Assistant embedding failed: {e}")

    # 3. Check and run summarization
    try:
        if await summarizer.should_summarize(conv_id):
            await summarizer.summarize(conv_id)
    except Exception as e:
        logger.warning(f"[Background] Summarization failed: {e}")

    # 4. Auto-title if this is the first message pair
    try:
        conv = await conversation_manager.get_conversation(conv_id)
        if conv and not conv.title and conv.message_count <= 2:
            # Generate a short title from the first user prompt
            title = user_prompt[:80].strip()
            if len(user_prompt) > 80:
                title = title.rsplit(" ", 1)[0] + "..."
            await conversation_manager.update_conversation_title(conv_id, title)
    except Exception as e:
        logger.warning(f"[Background] Auto-title failed: {e}")


# ---------------------------------------------------------------------------
# /edit — lightweight inline text editing (UNCHANGED)
# ---------------------------------------------------------------------------
_EDIT_SYSTEM_PROMPT = """You are a professional script editor.
The user will give you an instruction and a piece of selected text.
Apply the instruction to the selected text only.
Return ONLY the edited text — no explanations, no preamble, no quotes.
Preserve the original tone and style unless the instruction says otherwise."""


@app.post("/edit")
async def edit(
    instruction: str = Form(...),
    selected_text: str = Form(...),
):
    try:
        from config import get_genai_client
        genai_client = get_genai_client(location=STAGE_LOCATIONS.get("CRITIC", "global"))
        prompt = f"{_EDIT_SYSTEM_PROMPT}\n\nInstruction: {instruction}\n\nText:\n{selected_text}"
        response = await genai_client.aio.models.generate_content(
            model="projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview",
            contents=prompt,
        )
        return JSONResponse({"result": response.text.strip()})
    except Exception as e:
        logger.error(f"[/edit] Error: {e}")
        return JSONResponse({"result": None, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# /feedback — (UNCHANGED)
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
# /research — now stores briefs in DB instead of RAM cache
# ---------------------------------------------------------------------------
@app.post("/research")
async def run_research(
    client: str = Form(""),
    business_unit: str = Form(""),
    video_type: str = Form(""),
    video_tone: str = Form(""),
    duration: str = Form(""),
    prompt: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    metadata = {
        "client": client,
        "business_unit": business_unit,
        "video_type": video_type,
        "video_tone": video_tone,
        "duration": duration,
        "prompt": prompt,
    }

    file_parts = await parse_files(files or [], stage="NICHE_RESEARCH")

    stage = NicheResearchStage()
    result = await stage.run(metadata=metadata, file_parts=file_parts)

    research_id = str(uuid.uuid4())[:12]

    if result.success and result.data:
        # Store in DB instead of _research_cache
        await conversation_manager.save_research_brief(
            short_id=research_id,
            data=result.data,
            metadata=metadata,
        )

    return {
        "success": result.success,
        "research": result.data,
        "research_id": research_id,
        "error": result.error,
    }


# ---------------------------------------------------------------------------
# /messages — paginated message retrieval (enhanced)
# Now supports both chat_id (backward compat) and conversation_id
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
# /conversations — CRUD for conversation management (NEW)
# ---------------------------------------------------------------------------
@app.get("/conversations")
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List all conversations ordered by most recent activity."""
    conversations = await conversation_manager.list_conversations(
        limit=limit, offset=offset
    )
    return {
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "metadata": c.metadata,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
                "message_count": c.message_count,
            }
            for c in conversations
        ]
    }


@app.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get a single conversation with its summary."""
    conv = await conversation_manager.get_conversation(conv_id)
    if not conv:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)

    # Include the latest summary if available
    summary_data = await summarizer.get_latest_summary(conv_id)

    return {
        "id": conv.id,
        "title": conv.title,
        "metadata": conv.metadata,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "message_count": conv.message_count,
        "summary": summary_data.get("summary") if summary_data else None,
    }


@app.delete("/conversations/{conv_id}")
async def archive_conversation(conv_id: str):
    """Soft-delete (archive) a conversation."""
    success = await conversation_manager.archive_conversation(conv_id)
    if success:
        return {"status": "archived"}
    return JSONResponse({"error": "Failed to archive"}, status_code=500)
# import uuid, json, logging
# from typing import List, Optional
# from fastapi import FastAPI, UploadFile, File, Form, APIRouter
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import StreamingResponse, JSONResponse
# from config import init_vertex, STAGE_LOCATIONS
# from ingest.file_parser import parse_files
# from pipeline.orchestrator import run_pipeline
# from supabase import create_client
# from dotenv import load_dotenv
# from pipeline.fine_tune import export_training_jsonl, trigger_fine_tune_job
# import vertexai
# from vertexai.generative_models import GenerativeModel
# import os
# from pipeline.stages.niche_research import NicheResearchStage

# load_dotenv()
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

# # ---------------------------------------------------------------------------
# # In-memory research cache
# # Stores research briefs by short ID so they don't need to be passed
# # as large JSON form fields (which curl/browsers truncate or corrupt).
# # ---------------------------------------------------------------------------
# _research_cache: dict = {}

# app = FastAPI()

# # ---------------------------------------------------------------------------
# # CORS
# # Covers:
# #   • All Vercel preview + production deployments  (chatbot-*.vercel.app)
# #   • Local development                            (localhost:5173 / 3000)
# # ---------------------------------------------------------------------------
# _ALLOWED_ORIGINS = [
#     "http://localhost:5173",
#     "http://localhost:3000",
# ]

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=_ALLOWED_ORIGINS,
#     allow_origin_regex=r"https://chatbot-[a-zA-Z0-9\-]+\.vercel\.app",
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# @app.on_event("startup")
# async def startup():
#     init_vertex()
#     logger.info("✅ Vertex AI initialized")

# @app.get("/health")
# async def health():
#     """Quick endpoint to verify server is up."""
#     return {"status": "ok"}


# @app.post("/chat")
# async def chat(
#     prompt: str = Form(""),
#     debug: bool = Form(False),
#     # ── metadata + research ───────────────────────────────────
#     client:         str = Form(""),
#     business_unit:  str = Form(""),
#     video_type:     str = Form(""),
#     video_tone:     str = Form(""),
#     duration:       str = Form(""),
#     research_id:    str = Form(""),   # preferred: short ID looked up from cache
#     research_brief: str = Form(""),   # fallback: raw JSON string (may be truncated)
#     # ─────────────────────────────────────────────────────────
#     files: Optional[List[UploadFile]] = File(None),
# ):
#     trace_id = str(uuid.uuid4())[:8]
#     pipeline_trace = []

#     # Resolve research brief — prefer cache lookup over raw JSON string
#     parsed_research = None
#     if research_id and research_id in _research_cache:
#         parsed_research = _research_cache[research_id]
#         logger.info(f"[{trace_id}] Loaded research brief from cache (id={research_id})")
#     elif research_brief:
#         try:
#             parsed_research = json.loads(research_brief)
#             logger.info(f"[{trace_id}] Parsed research_brief from JSON string")
#         except Exception:
#             logger.warning(f"[{trace_id}] Could not parse research_brief JSON")

#     async def stream():
#         try:
#             file_parts = await parse_files(files or [], stage="VOICE_OVER")
#             async for chunk in run_pipeline(
#                 prompt=prompt,
#                 file_parts=file_parts,
#                 trace=pipeline_trace,
#                 client=client,
#                 business_unit=business_unit,
#                 video_type=video_type,
#                 video_tone=video_tone,
#                 duration=duration,
#                 research_brief=parsed_research,
#             ):
#                 yield chunk

#             if debug:
#                 yield f"debug:{json.dumps({'id': trace_id, 'trace': pipeline_trace})}\n"
#         except Exception as e:
#             logger.error(f"[{trace_id}] Unhandled stream error: {e}")
#             yield f"error:Server error — {str(e)}\n"

#     return StreamingResponse(stream(), media_type="text/plain")


# # ---------------------------------------------------------------------------
# # /edit — lightweight inline text editing, bypasses the full pipeline
# # ---------------------------------------------------------------------------
# _EDIT_SYSTEM_PROMPT = """You are a professional script editor.
# The user will give you an instruction and a piece of selected text.
# Apply the instruction to the selected text only.
# Return ONLY the edited text — no explanations, no preamble, no quotes.
# Preserve the original tone and style unless the instruction says otherwise."""

# @app.post("/edit")
# async def edit(
#     instruction: str = Form(...),
#     selected_text: str = Form(...),
# ):
#     try:
#         vertexai.init(project="poc-script-genai", location=STAGE_LOCATIONS.get("CRITIC", "global"))
#         model = GenerativeModel(
#             "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview"
#         )
#         prompt = f"{_EDIT_SYSTEM_PROMPT}\n\nInstruction: {instruction}\n\nText:\n{selected_text}"
#         response = await model.generate_content_async(prompt)
#         return JSONResponse({"result": response.text.strip()})
#     except Exception as e:
#         logger.error(f"[/edit] Error: {e}")
#         return JSONResponse({"result": None, "error": str(e)}, status_code=500)


# supabase_client = create_client(
#     os.getenv("SUPABASE_URL"),
#     os.getenv("SUPABASE_KEY")
# )

# @app.post("/feedback")
# async def feedback(
#     prompt: str = Form(""),
#     output: str = Form(""),
#     rating: int = Form(...),
# ):
#     print("FEEDBACK RECEIVED", prompt[:20], rating)
#     response = supabase_client.table("training_data").insert({
#         "prompt": prompt,
#         "output": output,
#         "rating": rating,
#     }).execute()
#     print("SUPABASE RESPONSE:", response)
#     return {"status": "saved"}

# @app.post("/research")
# async def run_research(
#     client: str = Form(""),
#     business_unit: str = Form(""),
#     video_type: str = Form(""),
#     video_tone: str = Form(""),
#     duration: str = Form(""),
#     prompt: str = Form(""),
# ):
#     metadata = {
#         "client": client,
#         "business_unit": business_unit,
#         "video_type": video_type,
#         "video_tone": video_tone,
#         "duration": duration,
#         "prompt": prompt,
#     }
#     stage = NicheResearchStage()
#     result = await stage.run(metadata=metadata)

#     # Store in cache and return a short ID — avoids passing large JSON as form field
#     research_id = str(uuid.uuid4())[:12]
#     if result.success and result.data:
#         _research_cache[research_id] = result.data
#         logger.info(f"[research] Cached brief (id={research_id}, keys={list(result.data.keys())})")

#     return {
#         "success": result.success,
#         "research": result.data,
#         "research_id": research_id,   # ← frontend should send this to /chat
#         "error": result.error,
#     }


# # @app.post("/admin/fine-tune")
# # async def start_fine_tune(secret: str = Form("")):
# #     # Basic protection — use a proper auth system in production
# #     if secret != os.environ.get("ADMIN_SECRET", ""):
# #         return {"error": "unauthorized"}

# #     path = await export_training_jsonl()
# #     if not path:
# #         return {"status": "not enough data yet"}

# #     job_name = await trigger_fine_tune_job(path)
# #     return {"status": "started", "job": job_name}






#  docker run -p 8080:8080 --env-file .env chatbot-backend-try2
# uvicorn api.index:app --reload --port 8000
# export GOOGLE_APPLICATION_CREDENTIALS="/Users/jayagrawal/Downloads/poc-script-genai-29c4a48586bf.json"







# import uuid, json, logging
# from typing import List, Optional
# from fastapi import FastAPI, UploadFile, File, Form, APIRouter, Query
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import StreamingResponse, JSONResponse
# from config import init_vertex, STAGE_LOCATIONS
# from ingest.file_parser import parse_files
# from pipeline.orchestrator import run_pipeline
# from supabase import create_client
# from dotenv import load_dotenv
# from pipeline.fine_tune import export_training_jsonl, trigger_fine_tune_job
# import vertexai
# from vertexai.generative_models import GenerativeModel
# import os
# from pipeline.stages.niche_research import NicheResearchStage
# load_dotenv()
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

# # ---------------------------------------------------------------------------
# # In-memory research cache
# # Stores research briefs by short ID so they don't need to be passed
# # as large JSON form fields (which curl/browsers truncate or corrupt).
# # ---------------------------------------------------------------------------
# _research_cache: dict = {}
# _script_cache: dict = {}

# app = FastAPI()

# # ---------------------------------------------------------------------------
# # CORS
# # Covers:
# #   • All Vercel preview + production deployments  (chatbot-*.vercel.app)
# #   • Local development                            (localhost:5173 / 3000)
# # ---------------------------------------------------------------------------
# _ALLOWED_ORIGINS = [
#     "http://localhost:5173",
#     "http://localhost:3000",
# ]

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=_ALLOWED_ORIGINS,
#     allow_origin_regex=r"https://chatbot-[a-zA-Z0-9\-]+\.vercel\.app",
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# @app.on_event("startup")
# async def startup():
#     init_vertex()
#     logger.info("✅ Vertex AI initialized")

# @app.get("/health")
# async def health():
#     """Quick endpoint to verify server is up."""
#     return {"status": "ok"}


# @app.post("/chat")
# async def chat(
#     prompt: str = Form(""),
#     debug: bool = Form(False),
#     # ── metadata + research ───────────────────────────────────
#     client:         str = Form(""),
#     business_unit:  str = Form(""),
#     video_type:     str = Form(""),
#     video_tone:     str = Form(""),
#     duration:       str = Form(""),
#     research_id:    str = Form(""),   # preferred: short ID looked up from cache
#     research_brief: str = Form(""),   # fallback: raw JSON string (may be truncated)
#     script_id: str = Form(""),
#     mode: str = Form("generate"),
#     files: Optional[List[UploadFile]] = File(None),
# ):
#     trace_id = str(uuid.uuid4())[:8]
#     pipeline_trace = []

#     # Resolve research brief — prefer cache lookup over raw JSON string
#     parsed_research = None
#     if research_id and research_id in _research_cache:
#         parsed_research = _research_cache[research_id]
#         logger.info(f"[{trace_id}] Loaded research brief from cache (id={research_id})")
#     elif research_brief:
#         try:
#             parsed_research = json.loads(research_brief)
#             logger.info(f"[{trace_id}] Parsed research_brief from JSON string")
#         except Exception:
#             logger.warning(f"[{trace_id}] Could not parse research_brief JSON")

#     # Resolve script — prefer cache lookup
#     existing_script = None
#     if mode == "edit" and script_id and script_id in _script_cache:
#         existing_script = _script_cache[script_id]
#         logger.info(f"[{trace_id}] Loaded script from cache (id={script_id})")
#     async def stream():
#         try:
#             file_parts = await parse_files(files or [], stage="VOICE_OVER")
#             async for chunk in run_pipeline(
#                 prompt=prompt,
#                 file_parts=file_parts,
#                 trace=pipeline_trace,
#                 client=client,
#                 business_unit=business_unit,
#                 video_type=video_type,
#                 video_tone=video_tone,
#                 duration=duration,
#                 research_brief=parsed_research,
#                 mode=mode,
#                 existing_script=existing_script,
#             ):
#                 yield chunk

#             if debug:
#                 yield f"debug:{json.dumps({'id': trace_id, 'trace': pipeline_trace})}\n"
#         except Exception as e:
#             logger.error(f"[{trace_id}] Unhandled stream error: {e}")
#             yield f"error:Server error — {str(e)}\n"

#     return StreamingResponse(stream(), media_type="text/plain")


# # ---------------------------------------------------------------------------
# # /edit — lightweight inline text editing, bypasses the full pipeline
# # ---------------------------------------------------------------------------
# _EDIT_SYSTEM_PROMPT = """You are a professional script editor.
# The user will give you an instruction and a piece of selected text.
# Apply the instruction to the selected text only.
# Return ONLY the edited text — no explanations, no preamble, no quotes.
# Preserve the original tone and style unless the instruction says otherwise."""

# @app.post("/edit")
# async def edit(
#     instruction: str = Form(...),
#     selected_text: str = Form(...),
# ):
#     try:
#         vertexai.init(project="poc-script-genai", location=STAGE_LOCATIONS.get("CRITIC", "global"))
#         model = GenerativeModel(
#             "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview"
#         )
#         prompt = f"{_EDIT_SYSTEM_PROMPT}\n\nInstruction: {instruction}\n\nText:\n{selected_text}"
#         response = await model.generate_content_async(prompt)
#         return JSONResponse({"result": response.text.strip()})
#     except Exception as e:
#         logger.error(f"[/edit] Error: {e}")
#         return JSONResponse({"result": None, "error": str(e)}, status_code=500)


# supabase_client = create_client(
#     os.getenv("SUPABASE_URL"),
#     os.getenv("SUPABASE_KEY")
# )

# @app.post("/feedback")
# async def feedback(
#     prompt: str = Form(""),
#     output: str = Form(""),
#     rating: int = Form(...),
# ):
#     print("FEEDBACK RECEIVED", prompt[:20], rating)
#     response = supabase_client.table("training_data").insert({
#         "prompt": prompt,
#         "output": output,
#         "rating": rating,
#     }).execute()
#     print("SUPABASE RESPONSE:", response)
#     return {"status": "saved"}

# # @app.post("/research")
# # async def run_research(
# #     client: str = Form(""),
# #     business_unit: str = Form(""),
# #     video_type: str = Form(""),
# #     video_tone: str = Form(""),
# #     duration: str = Form(""),
# #     prompt: str = Form(""),
    
# # ):
# #     metadata = {
# #         "client": client,
# #         "business_unit": business_unit,
# #         "video_type": video_type,
# #         "video_tone": video_tone,
# #         "duration": duration,
# #         "prompt": prompt,
# #     }
# #     stage = NicheResearchStage()
# #     result = await stage.run(metadata=metadata)

# #     # Store in cache and return a short ID — avoids passing large JSON as form field
# #     research_id = str(uuid.uuid4())[:12]
# #     if result.success and result.data:
# #         _research_cache[research_id] = result.data
# #         logger.info(f"[research] Cached brief (id={research_id}, keys={list(result.data.keys())})")

# #     return {
# #         "success": result.success,
# #         "research": result.data,
# #         "research_id": research_id,   # ← frontend should send this to /chat
# #         "error": result.error,
# #     }


# @app.post("/research")
# async def run_research(
#     client: str = Form(""),
#     business_unit: str = Form(""),
#     video_type: str = Form(""),
#     video_tone: str = Form(""),
#     duration: str = Form(""),
#     prompt: str = Form(""),
#     files: Optional[List[UploadFile]] = File(None),   # ✅ ADD THIS
# ):
#     metadata = {
#         "client": client,
#         "business_unit": business_unit,
#         "video_type": video_type,
#         "video_tone": video_tone,
#         "duration": duration,
#         "prompt": prompt,
#     }

#     # ✅ PARSE FILES
#     file_parts = await parse_files(files or [], stage="NICHE_RESEARCH")

#     stage = NicheResearchStage()

#     # ✅ PASS FILES INTO RESEARCH
#     result = await stage.run(
#         metadata=metadata,
#         file_parts=file_parts
#     )

#     research_id = str(uuid.uuid4())[:12]

#     if result.success and result.data:
#         _research_cache[research_id] = result.data

#     return {
#         "success": result.success,
#         "research": result.data,
#         "research_id": research_id,
#         "error": result.error,
#     }

# # ---------------------------------------------------------------------------
# # /messages — paginated message retrieval for infinite scroll
# # ---------------------------------------------------------------------------
# @app.get("/messages")
# async def get_messages(
#     chat_id: str = Query(..., description="Chat ID to fetch messages for"),
#     page: int = Query(1, ge=1, description="Page number (1-indexed)"),
#     limit: int = Query(20, ge=1, le=100, description="Messages per page"),
# ):
#     try:
#         offset = (page - 1) * limit

#         response = (
#             supabase_client
#             .table("messages")
#             .select("id, chat_id, content, created_at")
#             .eq("chat_id", chat_id)
#             .order("created_at", desc=True)
#             .range(offset, offset + limit - 1)
#             .execute()
#         )

#         messages = response.data or []
#         has_more = len(messages) == limit

#         # Reverse so frontend receives oldest → newest order
#         messages.reverse()

#         return {
#             "messages": messages,
#             "page": page,
#             "limit": limit,
#             "has_more": has_more,
#         }
#     except Exception as e:
#         logger.error(f"[/messages] Error fetching messages: {e}")
#         return JSONResponse(
#             {"messages": [], "page": page, "limit": limit, "has_more": False, "error": str(e)},
#             status_code=500,
#         )


# # @app.post("/admin/fine-tune")
# # async def start_fine_tune(secret: str = Form("")):
# #     # Basic protection — use a proper auth system in production
# #     if secret != os.environ.get("ADMIN_SECRET", ""):
# #         return {"error": "unauthorized"}

# #     path = await export_training_jsonl()
# #     if not path:
# #         return {"status": "not enough data yet"}

# #     job_name = await trigger_fine_tune_job(path)
# #     return {"status": "started", "job": job_name}











import uuid, json, logging
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from config import init_vertex, STAGE_LOCATIONS
from ingest.file_parser import parse_files
from pipeline.orchestrator import run_pipeline
from supabase import create_client
from dotenv import load_dotenv
from pipeline.fine_tune import export_training_jsonl, trigger_fine_tune_job
import vertexai
from vertexai.generative_models import GenerativeModel
import os
from pipeline.stages.niche_research import NicheResearchStage
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory research cache
# Stores research briefs by short ID so they don't need to be passed
# as large JSON form fields (which curl/browsers truncate or corrupt).
# ---------------------------------------------------------------------------
_research_cache: dict = {}
_script_cache: dict = {}
# Global fallback for latest script so the frontend doesn't need to send it
LAST_SCRIPT = None
LAST_SCRIPT_ID = None

app = FastAPI()

# ---------------------------------------------------------------------------
# CORS
# Covers:
#   • All Vercel preview + production deployments  (chatbot-*.vercel.app)
#   • Local development                            (localhost:5173 / 3000)
# ---------------------------------------------------------------------------
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
    init_vertex()
    logger.info("✅ Vertex AI initialized")

@app.get("/health")
async def health():
    """Quick endpoint to verify server is up."""
    return {"status": "ok"}


@app.post("/chat")
async def chat(
    prompt: str = Form(""),
    debug: bool = Form(False),
    # ── metadata + research ───────────────────────────────────
    client:         str = Form(""),
    business_unit:  str = Form(""),
    video_type:     str = Form(""),
    video_tone:     str = Form(""),
    duration:       str = Form(""),
    research_id:    str = Form(""),   # preferred: short ID looked up from cache
    research_brief: str = Form(""),   # fallback: raw JSON string (may be truncated)
    script_id: str = Form(""),
    mode: str = Form("generate"),
    files: Optional[List[UploadFile]] = File(None),
):
    trace_id = str(uuid.uuid4())[:8]
    pipeline_trace = []

    # Resolve research brief — prefer cache lookup over raw JSON string
    parsed_research = None
    if research_id and research_id in _research_cache:
        parsed_research = _research_cache[research_id]
        logger.info(f"[{trace_id}] Loaded research brief from cache (id={research_id})")
    elif research_brief:
        try:
            parsed_research = json.loads(research_brief)
            logger.info(f"[{trace_id}] Parsed research_brief from JSON string")
        except Exception:
            logger.warning(f"[{trace_id}] Could not parse research_brief JSON")

    global LAST_SCRIPT, LAST_SCRIPT_ID
    
    # ── AUTO-DETECT EDIT INTENT ──────────────────────────────
    EDIT_KEYWORDS = [
        "make it", "improve", "rewrite", "change tone",
        "make it more", "refine", "enhance"
    ]
    is_edit_intent = any(keyword in prompt.lower() for keyword in EDIT_KEYWORDS)
    
    if is_edit_intent and LAST_SCRIPT:
        mode = "edit"
        existing_script = LAST_SCRIPT
        logger.info(f"[{trace_id}] Auto-detected edit intent. Using LAST_SCRIPT.")
    else:
        # Resolve script — prefer cache lookup
        existing_script = None
        if mode == "edit" and script_id and script_id in _script_cache:
            existing_script = _script_cache[script_id]
            logger.info(f"[{trace_id}] Loaded script from cache (id={script_id})")
        
        # Safe fallback if edit requested but no script available
        if mode == "edit" and not existing_script:
            mode = "generate"
            logger.warning(f"[{trace_id}] Edit mode requested but no script found. Falling back to generate.")

    async def stream():
        global LAST_SCRIPT, LAST_SCRIPT_ID
        full_output = []
        try:
            file_parts = await parse_files(files or [], stage="VOICE_OVER")
            async for chunk in run_pipeline(
                prompt=prompt,
                file_parts=file_parts,
                trace=pipeline_trace,
                client=client,
                business_unit=business_unit,
                video_type=video_type,
                video_tone=video_tone,
                duration=duration,
                research_brief=parsed_research,
                mode=mode,
                existing_script=existing_script,
            ):
                if chunk.startswith("result:"):
                    full_output.append(chunk[7:].strip())
                yield chunk

            if debug:
                yield f"debug:{json.dumps({'id': trace_id, 'trace': pipeline_trace})}\n"
                
            # Auto-save the last script if we successfully generated/edited one
            if full_output:
                combined_script = "\n".join(full_output).strip()
                if combined_script:
                    LAST_SCRIPT = combined_script
                    new_id = script_id if script_id else str(uuid.uuid4())[:12]
                    LAST_SCRIPT_ID = new_id
                    _script_cache[new_id] = combined_script
                    logger.info(f"[{trace_id}] Auto-saved LAST_SCRIPT (id={new_id})")

        except Exception as e:
            logger.error(f"[{trace_id}] Unhandled stream error: {e}")
            yield f"error:Server error — {str(e)}\n"

    return StreamingResponse(stream(), media_type="text/plain")


# ---------------------------------------------------------------------------
# /edit — lightweight inline text editing, bypasses the full pipeline
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
        vertexai.init(project="poc-script-genai", location=STAGE_LOCATIONS.get("CRITIC", "global"))
        model = GenerativeModel(
            "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview"
        )
        prompt = f"{_EDIT_SYSTEM_PROMPT}\n\nInstruction: {instruction}\n\nText:\n{selected_text}"
        response = await model.generate_content_async(prompt)
        return JSONResponse({"result": response.text.strip()})
    except Exception as e:
        logger.error(f"[/edit] Error: {e}")
        return JSONResponse({"result": None, "error": str(e)}, status_code=500)


supabase_client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

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

    result = await stage.run(
        metadata=metadata,
        file_parts=file_parts
    )

    research_id = str(uuid.uuid4())[:12]

    if result.success and result.data:
        _research_cache[research_id] = result.data

    return {
        "success": result.success,
        "research": result.data,
        "research_id": research_id,
        "error": result.error,
    }

# ---------------------------------------------------------------------------
# /messages — paginated message retrieval for infinite scroll
# ---------------------------------------------------------------------------
@app.get("/messages")
async def get_messages(
    chat_id: str = Query(..., description="Chat ID to fetch messages for"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Messages per page"),
):
    try:
        offset = (page - 1) * limit

        response = (
            supabase_client
            .table("messages")
            # FIX 1: Added "role" so frontend can distinguish user vs assistant messages
            # Note: "prompt" column does not exist in the messages table;
            # the frontend already handles this gracefully with m.prompt ?? ""
            .select("id, chat_id, role, content, created_at")
            .eq("chat_id", chat_id)
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


# @app.post("/admin/fine-tune")
# async def start_fine_tune(secret: str = Form("")):
#     # Basic protection — use a proper auth system in production
#     if secret != os.environ.get("ADMIN_SECRET", ""):
#         return {"error": "unauthorized"}

#     path = await export_training_jsonl()
#     if not path:
#         return {"status": "not enough data yet"}

#     job_name = await trigger_fine_tune_job(path)
#     return {"status": "started", "job": job_name}
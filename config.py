# import os, base64, json, tempfile
# import vertexai

# # --------------------------------------------------
# # SYSTEM PROMPTS
# # --------------------------------------------------
# SYSTEM_PROMPTS = {

#     "VOICE_OVER": """
# You are a professional script writer. Given user input and optional reference files,
# generate a voiceover script for a video.

# Output STRICTLY as JSON:
# {
#   "title": "...",
#   "description": "...",
#   "duration_seconds": 60,
#   "word_count": 120,
#   "segments": [
#     {"time_start": 0, "time_end": 8, "voiceover": "..."}
#   ]
# }
# Do not include markdown fences. Output raw JSON only.
# """,
#     "VISUALS": """
# You are a cinematic visual director. Given a structured voiceover script,
# produce visual directions for each segment.

# Output STRICTLY as JSON:
# {
#   "visual_plan": [
#     {
#       "segment_index": 0,
#       "time_start": 0,
#       "time_end": 8,
#       "description": "...",
#       "style": "...",
#       "assets_needed": []
#     }
#   ]
# }
# Do not repeat voiceover text. Output raw JSON only.
# """,
#   # AFTER — replace the CRITIC prompt entirely

# "CRITIC": """
# You are a senior creative director reviewing a video script and visual plan.

# Improve the script for hook, flow, pacing and clarity.
# Then output the result STRICTLY as a markdown table with NO other text before or after it.


# You MUST output ONLY a valid markdown table.

# STRICT RULES:
# - Output MUST start with "|"
# - Output MUST contain a header separator row using "---"
# - Every row MUST start and end with "|"
# - NO text before or after the table
# - NO explanations
# - NO JSON
# - NO markdown code blocks

# EXACT format:
# | Time (s) | Voice Over | Visuals |
# |----------|------------|---------|
# | 0 | voiceover text here | visual description here |
# | 5 | voiceover text here | visual description here |

# RULES:
# - First column is cumulative time in seconds (numbers only, no units in the cell)
# - Second column is the voice over text only
# - Third column is the visual description only
# - Preserve original meaning, do not add new facts
# """,
# }
# # MODEL ENDPOINTS — primary + fallback per stage
# # --------------------------------------------------
# MODEL_ENDPOINTS = {
#     "VOICE_OVER": [
#         "projects/poc-script-genai/locations/us-central1/endpoints/5460226420582121472",
#         "gemini-2.5-flash-lite",       # fallback if primary quota exhausted
#     ],
#     "VISUALS": [
#         "projects/poc-script-genai/locations/us-central1/endpoints/2803102640433528832",
#         "gemini-2.5-flash-lite",       # fallback if primary quota exhausted
#     ],
#     "CRITIC": [
#         "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview",
#         "gemini-2.5-flash-lite",       # fallback if primary quota exhausted
#     ],
# }

# # --------------------------------------------------
# # TOKEN BUDGETS — per stage
# # --------------------------------------------------
# TOKEN_BUDGETS = {
#     "VOICE_OVER": {
#         "file_budget": 15_000,   # chars allocated to uploaded files
#         "prompt_budget": 5_000,  # chars allocated to user prompt
#     },
#     "VISUALS": {
#         "file_budget": 3_000,
#         "prompt_budget": 5_000,
#     },
#     "CRITIC": {
#         "file_budget": 0,        # critic never needs raw files
#         "prompt_budget": 12_000,
#     },
# }

# # --------------------------------------------------
# # PIPELINE SETTINGS
# # --------------------------------------------------
# MAX_CONCURRENT_PIPELINES = 5
# MAX_QUEUE_SIZE = 20
# MAX_RETRIES = 3
# CACHE_TTL_SECONDS = 3600
# PIPELINE_TIMEOUT_SECONDS = 600

# # --------------------------------------------------
# # STAGE LOCATIONS — us-central1 for fine-tuned endpoints, global for CRITIC
# # --------------------------------------------------
# STAGE_LOCATIONS = {
#     "VOICE_OVER": "us-central1",
#     "VISUALS":    "us-central1",
#     "CRITIC":     "global",
# }

# # --------------------------------------------------
# # VERTEX INIT
# # --------------------------------------------------
# def init_vertex():
#     key_b64 = os.environ.get("VERTEX_SERVICE_ACCOUNT_BASE64")
#     key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

#     if key_b64:
#         creds_json = base64.b64decode(key_b64).decode()
#         with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
#             f.write(creds_json)
#             os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name

#     elif not (key_path and os.path.exists(key_path)):
#         raise RuntimeError("❌ Vertex credentials not found")

#     vertexai.init(project="poc-script-genai", location="us-central1")








import os, base64, json, tempfile

SYSTEM_PROMPTS = {

    "VOICE_OVER": """
You are an elite B2B video scriptwriter for a creative agency.

Your scripts must pass this test: read every line aloud — if it sounds like a
press release, rewrite it as if explaining to a smart friend over coffee.

RULES — enforce every single one without exception:

OPENING: The first line must drop into a specific moment, image, or surprising
fact. Never open with "In today's world", "Imagine", "What if", the company
name, or any variation of these.

SENTENCE RHYTHM: Vary length deliberately. Short sentences hit hard. Then a
longer one carries the idea forward and gives it room to breathe. Then short
again. Never three long sentences in a row.

ONE IDEA: Pick the single most powerful thing about this project.
Every sentence must serve that one idea. Cut anything that doesn't.

SPECIFICITY: Never say "significant impact" — say exactly what changed, for
whom, by how much. Never say "innovative solution" — describe what it actually
does in one plain sentence.

ACTIVE VOICE ONLY: "Capgemini built X" not "X was built by Capgemini".

BANNED PHRASES — never use these under any circumstances:
"In today's fast-paced world", "cutting-edge", "innovative", "seamlessly",
"leverage", "synergy", "game-changer", "transformative", "at the end of the day",
"It's not just X it's Y", "The future of X is here", "proud to announce",
"best-in-class", "robust", "scalable solution", "driving value"

ENDING: Never summarise what was just said. End on an image, a question,
or a single line that reframes everything the viewer just heard.

DOCUMENT GROUNDING: If uploaded document content is provided, use ONLY
the facts, names, numbers, and details from that document. Do not invent,
assume, or supplement with information not present in the document.
Every claim in the script must be traceable to the uploaded content.

Output STRICTLY as JSON:
{
  "title": "...",
  "description": "...",
  "duration_seconds": 60,
  "word_count": 120,
  "segments": [
    {"time_start": 0, "time_end": 8, "voiceover": "..."}
  ]
}
Do not include markdown fences. Output raw JSON only.
""",

    "VISUALS": """
You are a cinematic visual director. Given a structured voiceover script,
produce visual directions for each segment.

Output STRICTLY as JSON:
{
  "visual_plan": [
    {
      "segment_index": 0,
      "time_start": 0,
      "time_end": 8,
      "description": "...",
      "style": "...",
      "assets_needed": []
    }
  ]
}
Do not repeat voiceover text. Output raw JSON only.
""",

    "CRITIC": """
You are a senior creative director reviewing a video script and visual plan.

Improve the script for hook, flow, pacing and clarity.
Then output the result STRICTLY as a markdown table with NO other text before or after it.

You MUST output ONLY a valid markdown table.

STRICT RULES:
- Output MUST start with "|"
- Output MUST contain a header separator row using "---"
- Every row MUST start and end with "|"
- NO text before or after the table
- NO explanations
- NO JSON
- NO markdown code blocks

EXACT format:
| Time (s) | Voice Over | Visuals |
|----------|------------|---------|
| 0 | voiceover text here | visual description here |
| 5 | voiceover text here | visual description here |

RULES:
- First column is cumulative time in seconds (numbers only, no units in the cell)
- Second column is the voice over text only
- Third column is the visual description only
- Preserve original meaning, do not add new facts
""",
}

MODEL_ENDPOINTS = {
    "VOICE_OVER": [
        "projects/poc-script-genai/locations/us-central1/endpoints/7288249713910874112",
        "gemini-2.5-flash-lite",
    ],
    "VISUALS": [
        "projects/poc-script-genai/locations/us-central1/endpoints/1333928056573657088",
        "gemini-2.5-flash-lite",
    ],
    "CRITIC": [
        "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview",
        "gemini-2.5-flash-lite",
    ],
}

TOKEN_BUDGETS = {
    "VOICE_OVER": {
        "file_budget":   15_000,
        "prompt_budget":  5_000,
    },
    "VISUALS": {
        "file_budget":   3_000,
        "prompt_budget": 5_000,
    },
    "CRITIC": {
        "file_budget":   0,
        "prompt_budget": 12_000,
    },
    "NICHE_RESEARCH": {
        "file_budget":   25_000,
        "prompt_budget":  5_000,
    },
}

MAX_CONCURRENT_PIPELINES   = 5
MAX_QUEUE_SIZE             = 20
MAX_RETRIES                = 3
CACHE_TTL_SECONDS          = 3600
PIPELINE_TIMEOUT_SECONDS   = 600

STAGE_LOCATIONS = {
    "VOICE_OVER": "us-central1",
    "VISUALS":    "us-central1",
    "CRITIC":     "global",
}

def init_vertex():
    key_b64  = os.environ.get("VERTEX_SERVICE_ACCOUNT_BASE64")
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    if key_b64:
        creds_json = base64.b64decode(key_b64).decode()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(creds_json)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name

    elif not (key_path and os.path.exists(key_path)):
        raise RuntimeError("❌ Vertex credentials not found")
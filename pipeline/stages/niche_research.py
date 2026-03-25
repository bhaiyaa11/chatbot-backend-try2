import json
import logging
import asyncio
import httpx
import re
import os
import tempfile
import threading
from urllib.parse import quote   # ← ADD THIS

from youtube_transcript_api import YouTubeTranscriptApi

from pipeline.contracts import StageResult

import vertexai
from vertexai.generative_models import GenerativeModel

logger = logging.getLogger(__name__)

WORKING_MODEL          = "projects/poc-script-genai/locations/global/publishers/google/models/gemini-3-flash-preview"
VERTEX_SEARCH_PROJECT  = "poc-script-genai"
VERTEX_SEARCH_LOCATION = "global"
VERTEX_SEARCH_APP_ID   = "script-research_1773405109220"

MAX_GEMINI_CONCURRENCY = 3
SEMAPHORE = asyncio.Semaphore(MAX_GEMINI_CONCURRENCY)

# --------------------------------------------------
# Whisper — lazy loaded, thread-safe
# --------------------------------------------------
_whisper_model = None
_whisper_lock  = threading.Lock()

def _get_whisper():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            logger.info("[NicheResearch] Loading Whisper model (first use)...")
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel("base")
            logger.info("[NicheResearch] Whisper model loaded ✅")
    return _whisper_model


# ==================================================
# PROMPTS
# ==================================================

PROJECT_EXTRACTOR_PROMPT = """
You are a research analyst. A user has typed a free-form prompt describing
a video they want to create. Extract the key entities needed for research.

User prompt: {prompt}
Client field: {client}
Business unit: {business_unit}

Return STRICTLY as JSON — no fences:
{{
  "company_name": "The main company/brand (e.g. Capgemini, Renault, IBM)",
  "project_name": "The specific project, product, or collaboration (e.g. Tara Solar, Brand Evolution, Envizi)",
  "industry": "The industry this belongs to (e.g. Sustainability AI, Automotive, ESG)",
  "buyer_persona": "Who buys or cares about this (e.g. Chief Sustainability Officer, CMO, CTO)",
  "search_queries": [
    "Specific search query to find project facts on the company website",
    "News/press release search query for this project",
    "Competitor or market context search query"
  ]
}}
"""

PROJECT_SUMMARY_PROMPT = """
You are extracting factual project intelligence for a video scriptwriter.

Raw content from search results about: {project}

{raw_content}

Extract and structure ALL factual details:
- What the project/product actually is
- The company's specific role and contribution
- Key technical differentiators (real ones, not marketing fluff)
- Human impact — who benefits and how
- Timeline, milestones, scale
- Any quotes, statistics, or proof points
- What makes this different from competitors

Return as clear structured text. If content is irrelevant, say "No relevant project data found."
"""

QUERY_PROMPT = """
You are a research strategist for a B2B video ad agency.

Generate targeted search queries for this campaign brief.

Client: {client}
Project: {project_name}
Industry: {business_unit}
Buyer Persona: {buyer_persona}
Video Type: {video_type}
Tone: {video_tone}

WEB QUERIES: Target competitor campaigns, buyer pain points, messaging strategy, industry trends.
Good: "Capgemini sustainability AI enterprise product launch campaign 2024"

YOUTUBE QUERIES: Target content LIKELY TO HAVE OPEN TRANSCRIPTS:
- Conference keynotes and panel discussions (almost always transcribed)
- Analyst and thought leadership talks
- TED / TEDx talks on the topic
- Buyer interviews and "lessons learned" content
- Educational explainers on the pain points

DO NOT search for polished brand ads — they rarely have accessible transcripts.
Good: "Chief Sustainability Officer ESG data challenges keynote 2024"
Bad: "SAP Sustainability Control Tower product launch video"

Return STRICTLY as JSON — no markdown fences:
{{
  "niche_summary": "One sentence describing the exact niche and buyer.",
  "web_queries": ["query 1", "query 2", "query 3", "query 4"],
  "youtube_queries": ["query 1", "query 2", "query 3"]
}}
"""

TRANSCRIPT_ANALYSIS_PROMPT = """
You are a B2B creative director analyzing a real video transcript
to extract copywriting intelligence for a script writer.

Video title: {title}
Channel: {channel}

Opening Hook (first ~120 words):
{hook_segment}

Full Transcript:
{transcript}

Output STRICTLY as JSON — no fences:
{{
  "hook": "The exact opening line or first 10 seconds of the script",
  "hook_type": "One of: stat-led / question / scenario / contrast / bold-claim / emotional",
  "structure": "How the video is structured in 1-2 sentences",
  "key_phrases": ["memorable phrase 1", "memorable phrase 2", "memorable phrase 3"],
  "cta": "The closing call to action",
  "tone": "2-3 words describing the tone",
  "what_works": "One sentence on what makes this script effective",
  "what_to_steal": "One specific technique a copywriter should borrow from this script"
}}
"""

SYNTHESIS_PROMPT = """
You are a senior B2B creative strategist synthesizing research
into actionable intelligence for a video ad scriptwriter.

PROJECT INTELLIGENCE (verified facts about this specific project — use these verbatim):
{project_intelligence}

NICHE RESEARCH (buyer pain points, hooks, competitor landscape from real content):
{research_content}

Campaign Brief:
Client: {client}
Project: {project_name}
Industry: {business_unit}
Video Type: {video_type}
Tone: {video_tone}

Output STRICTLY as JSON — no markdown fences, no extra keys:
{{
  "niche_summary": "2-3 sentences on this niche: who the buyers are, what they care about, what the market looks like right now",
  "project_facts": "3-5 specific verified facts about this project that MUST appear in the script — include real names, numbers, roles",
  "top_pain_points": [
    "Specific pain point 1 grounded in research",
    "Specific pain point 2 grounded in research",
    "Specific pain point 3 grounded in research"
  ],
  "winning_hooks": [
    "Hook pattern 1 with EXACT technique — e.g. Stat-led: Opens with X% of CSOs failing to hit targets",
    "Hook pattern 2",
    "Hook pattern 3"
  ],
  "proven_phrases": [
    "Phrase actually found in transcripts or competitor content",
    "Phrase 2",
    "Phrase 3"
  ],
  "tone_patterns": [
    "Specific tone observation backed by evidence",
    "Tone observation 2"
  ],
  "competitor_landscape": "2-3 sentences on how competitors are positioning and what gaps exist",
  "recommended_angle": "The single strongest creative angle for this specific project + niche combination",
  "words_that_resonate": ["word1", "word2", "word3", "word4", "word5"],
  "words_to_avoid": ["cliche1", "cliche2", "cliche3"]
}}
"""


# ==================================================
# Gemini helpers
# ==================================================

def _get_model() -> GenerativeModel:
    vertexai.init(project="poc-script-genai", location="global")
    return GenerativeModel(WORKING_MODEL)


async def _call_model(prompt: str) -> str:
    async with SEMAPHORE:
        model = _get_model()
        response = await model.generate_content_async(prompt)
        return response.text or ""


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


async def _tavily_search(query: str, max_results: int = 5) -> list:
    """
    Search using Tavily — returns clean extracted content, no HTML parsing needed.
    Returns list of {title, link, snippet, content} dicts.
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        logger.warning("[NicheResearch] TAVILY_API_KEY not set")
        return []

    payload = {
        "api_key":          api_key,
        "query":            query,
        "max_results":      max_results,
        "search_depth":     "advanced",   # deep search — fetches full page content
        "include_answer":   True,         # Tavily AI answer summarising results
        "include_raw_content": False,     # cleaned content is enough
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res  = await client.post("https://api.tavily.com/search", json=payload)
            data = res.json()

            results = []

            # Add Tavily's own AI answer first if available — it's usually excellent
            answer = data.get("answer", "")
            if answer:
                results.append({
                    "title":   "Tavily AI Answer",
                    "link":    "",
                    "content": answer,
                })

            for item in data.get("results", []):
                results.append({
                    "title":   item.get("title", ""),
                    "link":    item.get("url", ""),
                    "content": item.get("content", "") or item.get("snippet", ""),
                })

            logger.info(f"[NicheResearch] Tavily found {len(results)} results for: {query[:60]}")
            return results

    except Exception as e:
        logger.warning(f"[NicheResearch] Tavily search failed for '{query}': {e}")
        return []


async def _vertex_search(query: str, page_size: int = 5) -> list:
    """
    Vertex AI Search — used for niche/competitor research.
    Falls back gracefully if not configured.
    """
    api_key = os.getenv("VERTEX_SEARCH_API_KEY", "")
    if not api_key:
        return []

    serving_config = (
        f"projects/{VERTEX_SEARCH_PROJECT}/locations/{VERTEX_SEARCH_LOCATION}"
        f"/collections/default_collection/engines/{VERTEX_SEARCH_APP_ID}"
        f"/servingConfigs/default_config"
    )
    url = f"https://discoveryengine.googleapis.com/v1/{serving_config}:searchLite?key={api_key}"

    payload = {
        "query": query,
        "pageSize": page_size,
        "contentSearchSpec": {
            "snippetSpec": {"returnSnippet": True, "maxSnippetCount": 2},
            "summarySpec": {"summaryResultCount": 3, "includeCitations": True},
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res  = await client.post(url, json=payload)
            data = res.json()
            results = []
            for item in data.get("results", []):
                derived  = item.get("document", {}).get("derivedStructData", {})
                title    = derived.get("title", "")
                link     = derived.get("link", "")
                snippets = derived.get("snippets", [])
                snippet  = " ".join(s.get("snippet", "") for s in snippets if s.get("snippet"))
                if title or snippet:
                    results.append({"title": title, "link": link, "snippet": snippet})
            summary = data.get("summary", {}).get("summaryText", "")
            if summary:
                results.append({"title": "AI Summary", "link": "", "snippet": summary})
            return results
    except Exception as e:
        logger.warning(f"[NicheResearch] Vertex Search failed for '{query}': {e}")
        return []


# ==================================================
# PHASE 1 — Project Intelligence (Tavily web search)
# ==================================================

async def _extract_project_intelligence(metadata: dict) -> tuple:
    prompt_text   = metadata.get("prompt", "")
    client        = metadata.get("client", "")
    business_unit = metadata.get("business_unit", "")

    # ── Step 1: Extract entities from prompt ─────────────────────
    logger.info("[NicheResearch] Extracting project entities from prompt...")
    extractor_raw = await _call_model(
        PROJECT_EXTRACTOR_PROMPT.format(
            prompt=prompt_text,
            client=client,
            business_unit=business_unit,
        )
    )

    try:
        entities = _parse_json(extractor_raw)
    except Exception:
        entities = {
            "company_name":   client,
            "project_name":   prompt_text[:60],
            "industry":       business_unit,
            "buyer_persona":  "Business decision maker",
            "search_queries": [f"{client} {prompt_text[:50]}"]
        }

    company = entities.get("company_name", client)
    project = entities.get("project_name", "")
    queries = entities.get("search_queries", [f"{company} {project}"])

    logger.info(f"[NicheResearch] Identified: {company} — {project}")
    logger.info(f"[NicheResearch] Project search queries: {queries}")

    # ── Step 2: Tavily search — full web, clean content ──────────
    search_tasks = [_tavily_search(q, max_results=5) for q in queries[:3]]
    search_results_nested = await asyncio.gather(*search_tasks)

    # Flatten + deduplicate by URL
    seen_urls, all_results = set(), []
    for results in search_results_nested:
        for r in results:
            if r["link"] not in seen_urls:
                seen_urls.add(r["link"])
                all_results.append(r)

    if not all_results:
        logger.info("[NicheResearch] No web results found — using prompt only")
        return f"Project: {company} — {project}\nNo additional project data found.", entities

    logger.info(f"[NicheResearch] Got {len(all_results)} sources from Tavily")

    # ── Step 3: Gemini summarises all sources ─────────────────────
    source_blocks = []
    for r in all_results:
        content = r.get("content", "")
        if content:
            source_blocks.append(
                f"SOURCE: {r['title']}\n"
                f"URL: {r['link']}\n"
                f"CONTENT: {content}"
            )

    if not source_blocks:
        logger.info("[NicheResearch] No content extracted — using prompt only")
        return f"Project: {company} — {project}\nNo additional project data found.", entities

    raw_content = "\n\n---\n\n".join(source_blocks)
    logger.info(f"[NicheResearch] Summarising {len(source_blocks)} sources into project facts...")

    summary = await _call_model(
        PROJECT_SUMMARY_PROMPT.format(
            project=f"{company} — {project}",
            raw_content=raw_content[:10000],
        )
    )

    logger.info(f"[NicheResearch] ✅ Project intelligence extracted ({len(summary)} chars)")
    return summary, entities


# ==================================================
# Audio + Whisper
# ==================================================

def _download_audio(video_id: str) -> str | None:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return None

    url = f"https://youtube.com/watch?v={video_id}"
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "match_filter": lambda info: None if (info.get("duration") or 9999) <= 600 else "Video too long — skipping",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        for file in os.listdir(temp_dir):
            if file.endswith(".mp3"):
                return os.path.join(temp_dir, file)
    except Exception as e:
        logger.warning(f"[NicheResearch] yt-dlp download failed: {e}")
    return None


def _transcribe_audio(path: str) -> str | None:
    try:
        whisper = _get_whisper()
        segments, _ = whisper.transcribe(path)
        return " ".join([seg.text for seg in segments])
    except Exception as e:
        logger.warning(f"[NicheResearch] Whisper transcription failed: {e}")
        return None


def _get_transcript(video_id: str) -> str | None:
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
        text = " ".join([t["text"] for t in transcript])
        if len(text) < 50:
            return None
        logger.info(f"[NicheResearch] ✅ YouTube transcript: {video_id} ({len(text)} chars)")
        return text
    except Exception:
        logger.info(f"[NicheResearch] No YouTube transcript for {video_id} — trying Whisper fallback")

    audio_path = _download_audio(video_id)
    if not audio_path:
        return None

    text = _transcribe_audio(audio_path)
    try:
        os.remove(audio_path)
    except Exception:
        pass

    if not text or len(text) < 50:
        logger.info(f"[NicheResearch] Whisper transcript too short, skipping: {video_id}")
        return None

    logger.info(f"[NicheResearch] ✅ Whisper transcript: {video_id} ({len(text)} chars)")
    return text


# ==================================================
# Video stats
# ==================================================

async def _fetch_video_stats(video_ids: list, api_key: str) -> dict:
    if not video_ids:
        return {}
    url = (
        f"https://www.googleapis.com/youtube/v3/videos"
        f"?part=statistics,contentDetails&id={','.join(video_ids)}&key={api_key}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url)
            stats = {}
            for item in res.json().get("items", []):
                vid = item["id"]
                s   = item.get("statistics", {})
                views    = int(s.get("viewCount", 0))
                likes    = int(s.get("likeCount", 0))
                comments = int(s.get("commentCount", 0))
                duration_str = item.get("contentDetails", {}).get("duration", "PT0S")
                match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
                h, m, s_ = (int(x) if x else 0 for x in match.groups())
                stats[vid] = {
                    "views":      views,
                    "likes":      likes,
                    "comments":   comments,
                    "duration":   h * 3600 + m * 60 + s_,
                    "engagement": views + (likes * 20) + (comments * 50),
                }
            return stats
    except Exception as e:
        logger.warning(f"[NicheResearch] Stats fetch failed: {e}")
        return {}


# ==================================================
# Per-video transcript analysis
# ==================================================

async def _fetch_and_analyze_transcript(video: dict) -> dict | None:
    video_id = video["video_id"]
    transcript = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _get_transcript(video_id)
    )
    if not transcript:
        return None

    hook_segment = " ".join(transcript.split()[:120])
    prompt = TRANSCRIPT_ANALYSIS_PROMPT.format(
        title=video["title"],
        channel=video["channel"],
        hook_segment=hook_segment,
        transcript=transcript[:4000],
    )

    try:
        analysis_raw = await _call_model(prompt)
        analysis     = _parse_json(analysis_raw)
        return {
            "title":    video["title"],
            "channel":  video["channel"],
            "url":      f"https://youtube.com/watch?v={video_id}",
            "views":    video.get("views", 0),
            "analysis": analysis,
        }
    except Exception as e:
        logger.warning(f"[NicheResearch] Analysis failed for '{video['title']}': {e}")
        return None


# ==================================================
# PHASE 2 — Niche Intelligence
# ==================================================

async def _run_youtube_searches(queries: list) -> tuple:
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        return "YouTube search not configured.", 0

    video_candidates = []
    async with httpx.AsyncClient(timeout=15) as client:
        for query in queries[:3]:
            try:
                url = (
                    f"https://www.googleapis.com/youtube/v3/search"
                    f"?key={api_key}&q={quote(query)}"
                    f"&part=snippet&type=video&maxResults=10&relevanceLanguage=en"
                )
                res   = await client.get(url)
                for item in res.json().get("items", []):
                    vid = item["id"].get("videoId")
                    if vid:
                        video_candidates.append({
                            "video_id": vid,
                            "title":    item["snippet"]["title"],
                            "channel":  item["snippet"]["channelTitle"],
                        })
            except Exception as e:
                logger.warning(f"[NicheResearch] YouTube search failed for '{query}': {e}")

    seen, unique = set(), []
    for v in video_candidates:
        if v["video_id"] not in seen:
            seen.add(v["video_id"])
            unique.append(v)

    logger.info(f"[NicheResearch] {len(unique)} unique video candidates")

    stats = await _fetch_video_stats([v["video_id"] for v in unique], api_key)
    for v in unique:
        s = stats.get(v["video_id"], {})
        v["views"]      = s.get("views", 0)
        v["engagement"] = s.get("engagement", 0)
        v["duration"]   = s.get("duration", 0)

    before = len(unique)
    unique = [v for v in unique if v["duration"] <= 600]
    if before - len(unique):
        logger.info(f"[NicheResearch] Skipped {before - len(unique)} videos over 10 minutes")

    unique.sort(key=lambda x: x["engagement"], reverse=True)
    top_videos = unique[:8]
    logger.info(f"[NicheResearch] Analyzing top {len(top_videos)} videos by engagement...")

    results = [r for r in await asyncio.gather(
        *[_fetch_and_analyze_transcript(v) for v in top_videos]
    ) if r is not None]

    transcript_count = len(results)
    logger.info(f"[NicheResearch] Successfully analyzed {transcript_count} transcripts")

    if not results:
        return "No transcripts found for this niche.", 0

    formatted = []
    for r in results:
        a = r["analysis"]
        formatted.append(
            f"VIDEO: {r['title']}\nCHANNEL: {r['channel']}\nVIEWS: {r['views']:,}\n"
            f"URL: {r['url']}\nHook: {a.get('hook')}\nHook Type: {a.get('hook_type')}\n"
            f"Structure: {a.get('structure')}\nKey Phrases: {', '.join(a.get('key_phrases') or [])}\n"
            f"CTA: {a.get('cta')}\nTone: {a.get('tone')}\n"
            f"What Works: {a.get('what_works')}\nWhat To Steal: {a.get('what_to_steal')}\n"
        )

    return "\n---\n".join(formatted), transcript_count


async def _run_web_searches(queries: list) -> str:
    results = []
    for query in queries[:4]:
        items = await _vertex_search(query, page_size=3)
        for item in items:
            results.append(f"SOURCE: {item['title']}\nURL: {item['link']}\n{item['snippet']}\n")
    return "\n---\n".join(results) if results else "No web results found."


# ==================================================
# Main Stage
# ==================================================

class NicheResearchStage:

    async def run(self, metadata: dict, file_parts: list = None) -> StageResult:
        try:
            # ── Phase 1: Project Intelligence ────────────────────
            logger.info("[NicheResearch] Phase 1 — Extracting project intelligence...")
            project_intel, entities = await _extract_project_intelligence(metadata, file_parts=file_parts)

            # ── Phase 2: Niche queries ────────────────────────────
            logger.info("[NicheResearch] Phase 2 — Generating niche search queries...")
            queries_raw = await _call_model(
                QUERY_PROMPT.format(
                    client=metadata.get("client", "Unknown"),
                    project_name=entities.get("project_name", ""),
                    business_unit=metadata.get("business_unit", "Unknown"),
                    buyer_persona=entities.get("buyer_persona", "Business leader"),
                    video_type=metadata.get("video_type", "Unknown"),
                    video_tone=metadata.get("video_tone", "Unknown"),
                )
            )
            queries = _parse_json(queries_raw)

            web_queries = queries.get("web_queries", [])
            yt_queries  = queries.get("youtube_queries", [])

            logger.info(f"[NicheResearch] Niche: {queries.get('niche_summary')}")
            logger.info(f"[NicheResearch] Web queries: {web_queries}")
            logger.info(f"[NicheResearch] YouTube queries: {yt_queries}")

            # ── Phase 2: Run web + YouTube in parallel ────────────
            logger.info("[NicheResearch] Running web + YouTube searches...")
            web_content, (youtube_content, transcript_count) = await asyncio.gather(
                _run_web_searches(web_queries),
                _run_youtube_searches(yt_queries),
            )

            logger.info(f"[NicheResearch] Transcripts analyzed: {transcript_count}")

            # ── Phase 3: Synthesise ───────────────────────────────
            logger.info("[NicheResearch] Synthesizing all research...")
            synthesis_raw = await _call_model(
                SYNTHESIS_PROMPT.format(
                    project_intelligence=project_intel,
                    research_content=(
                        f"WEB RESEARCH:\n{web_content}\n\n"
                        f"YOUTUBE ANALYSES ({transcript_count} videos):\n{youtube_content}"
                    )[:12000],
                    client=metadata.get("client", "Unknown"),
                    project_name=entities.get("project_name", ""),
                    business_unit=metadata.get("business_unit", "Unknown"),
                    video_type=metadata.get("video_type", "Unknown"),
                    video_tone=metadata.get("video_tone", "Unknown"),
                )
            )

            research_brief = _parse_json(synthesis_raw)
            research_brief["niche_summary_title"] = queries.get("niche_summary", "")
            research_brief["transcript_count"]     = transcript_count
            research_brief["project_intelligence"] = project_intel  # raw facts for VoiceOver

            logger.info(f"[NicheResearch] ✅ Complete — {transcript_count} transcripts analyzed")

            return StageResult(stage="niche_research", success=True, data=research_brief)

        except Exception as e:
            logger.error(f"[NicheResearch] ❌ Failed: {e}")
            return StageResult(stage="niche_research", success=False, data=None, error=str(e))
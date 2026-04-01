from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any, Union


# ── Stage 1 Output ──────────────────────────────────────────────
class ScriptSegment(BaseModel):
    time_start: int
    time_end: int
    voiceover: str


class VoiceOverOutput(BaseModel):
    title: str
    description: str
    duration_seconds: int
    word_count: int
    segments: List[ScriptSegment]
    internal_sources: List[str] = Field(default_factory=list)
    web_sources: List[str] = Field(default_factory=list)

    def to_visuals_input(self) -> dict:
        """Minimal representation for visuals stage — saves ~40% tokens."""
        return {
            "segments": [
                {"t": f"{s.time_start}-{s.time_end}", "vo": s.voiceover}
                for s in self.segments
            ]
        }


# ── Stage 2 Output ──────────────────────────────────────────────
class VisualScene(BaseModel):
    segment_index: int
    time_start: int
    time_end: int
    description: str
    style: str
    assets_needed: List[str] = Field(default_factory=list)


class VisualsOutput(BaseModel):
    visual_plan: List[VisualScene]


# ── Stage 3 Output ──────────────────────────────────────────────

# AFTER

# ── Frontend Output ─────────────────────────────────────────────
class SceneRow(BaseModel):
    timestamp: str
    voiceover: str
    visual: str
    notes: str


class FrontendOutput(BaseModel):
    title: str
    description: str
    duration_seconds: int
    improvements: List[str]
    scenes: List[SceneRow]


# ── Pipeline Envelope ───────────────────────────────────────────
class StageResult(BaseModel):
    stage: str
    success: bool
    data: Optional[Any] = None        # can be VoiceOverOutput, VisualsOutput, or str
    error: Optional[str] = None
    duration_ms: int = 0
    attempts: int = 0
    cache_hit: bool = False
    source_tokens: int = 0
    source_type: Optional[str] = None  # text_pdf | image_pdf | image | none
    grounded: bool = True
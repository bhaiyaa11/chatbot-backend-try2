from pydantic import BaseModel
from typing import List

class CreativeReviewRequest(BaseModel):

    prompt: str

    client: str = ""
    business_unit: str = ""
    video_type: str = ""
    video_tone: str = ""
    duration: str = ""

    creativity_ratio: float = 0.5


class CreativeReviewResponse(BaseModel):
    review_id: str                    # ← add

    retrievals: List[dict] = []       # ← add

    semantic_inspiration: dict

    essences: List[str]

    interpretations: List[str]

    creative_summary: str
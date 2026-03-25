# import json
# from pipeline.stages.base import BaseStage
# from pipeline.contracts import VoiceOverOutput, VisualsOutput
# from pipeline.llm_client import call_llm
# from config import SYSTEM_PROMPTS


# class VisualsStage(BaseStage):
#     name = "VISUALS"

#     async def execute(self, voice_over: VoiceOverOutput, file_parts: list,) -> VisualsOutput:
#         # Use compressed representation — saves ~40% tokens vs full model_dump()
#         script_context = json.dumps(voice_over.to_visuals_input(), indent=2)

#         contents = [SYSTEM_PROMPTS["VISUALS"], script_context]

#         raw, attempts, cache_hit = await call_llm("VISUALS", contents)
#         return VisualsOutput(**raw)






import json
from pipeline.stages.base import BaseStage
from pipeline.contracts import VoiceOverOutput, VisualsOutput
from pipeline.llm_client import call_llm
from config import SYSTEM_PROMPTS


class VisualsStage(BaseStage):
    name = "VISUALS"

    async def execute(
        self,
        voice_over: VoiceOverOutput,
        file_parts: list,
        metadata: dict = None,        # ← accepted, not used yet
        research_brief: dict = None,  # ← accepted, not used yet
    ) -> VisualsOutput:
        # Use compressed representation — saves ~40% tokens vs full model_dump()
        script_context = json.dumps(voice_over.to_visuals_input(), indent=2)

        contents = [SYSTEM_PROMPTS["VISUALS"], script_context]

        raw, attempts, cache_hit = await call_llm("VISUALS", contents)
        return VisualsOutput(**raw)
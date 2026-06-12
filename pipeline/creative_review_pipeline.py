from pipeline.stages.rag_retrieval import (
    RAGRetrievalStage
)

from pipeline.semantic_distillation import (
    SemanticDistillationEngine
)

from pipeline.stages.essence_generator import (
    generate_essences
)

from pipeline.stages.interpretation_generator import (
    generate_interpretations
)

from pipeline.stages.creative_summary import (
    generate_summary
)


async def run_generate_script_pipeline(
    prompt,
    context,
    approved_payload,
    file_parts,
    client="",
    business_unit="",
    video_type="",
    video_tone="",
    duration="",
    preferences=None,
):
    """
    Takes approved essences/interpretations and generates final script.
    """

    yield "status:Generating voiceover...\n"

    # TEMPORARY PLACEHOLDER

    yield "result:Generate Script Pipeline Not Implemented Yet\n"

async def run_creative_review(
    prompt,
    metadata,
    creativity_ratio,
    file_parts=None,
):

    rag = await RAGRetrievalStage().run(
        prompt=prompt,
        metadata=metadata
    )

    chunks = rag.data

    sie = SemanticDistillationEngine()

    sie_result = await sie.process(
        retrieved_chunks=chunks,
        creativity_ratio=creativity_ratio
    )

    semantic_inspiration = (
        sie_result["semantic_inspiration"]
    )

    essences = await generate_essences(
        semantic_inspiration
    )

    interpretations = (
        await generate_interpretations(
            essences["essences"]
        )
    )

    summary = await generate_summary(
        essences["essences"],
        interpretations["interpretations"]
    )

    return {

        "semantic_inspiration":
        semantic_inspiration,

        "essences":
        essences["essences"],

        "interpretations":
        interpretations["interpretations"],

        "creative_summary":
        summary
    }

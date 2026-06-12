from anthropic import AsyncAnthropic
import os

client = AsyncAnthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

async def generate_summary(
    essences,
    interpretations
):

    prompt = f"""
    Create a concise creative direction.

    Essences:
    {essences}

    Interpretations:
    {interpretations}

    Requirements:

    - 30-60 words
    - plain text only
    - no markdown
    - no bullet points
    - no title

    Return only the summary.
    """

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        temperature=0.6,
        messages=[
            {
                "role":"user",
                "content":prompt
            }
        ]
    )

    return response.content[0].text.strip()
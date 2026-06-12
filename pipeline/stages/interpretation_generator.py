from anthropic import AsyncAnthropic
import os
import json
import re

client = AsyncAnthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

PROMPT = """
You are a Narrative Interpretation Generator.

Input:
Narrative essences.

Generate concise narrative observations.

Rules:

- One sentence each
- Maximum 25 words
- Human editable
- No titles
- No metadata
- No nested objects

Return JSON ONLY:

{
  "interpretations": [
    "The city seemed emotionally vacant.",
    "Connection felt increasingly rare.",
    "Silence became its own character."
  ]
}
"""

async def generate_interpretations(
    essences: list
):

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        temperature=0.8,
        messages=[
            {
                "role":"user",
                "content":
                PROMPT +
                "\n\n" +
                json.dumps(essences)
            }
        ]
    )

    # return json.loads(
    #     response.content[0].text
    # )

    raw = response.content[0].text

    print("\n==============================")
    print("INTERPRETATION RAW RESPONSE")
    print("==============================")
    print(raw)
    print("==============================\n")

    clean = raw.strip()

    clean = re.sub(
        r"^```json",
        "",
        clean,
        flags=re.IGNORECASE
    )

    clean = re.sub(
        r"^```",
        "",
        clean
    )

    clean = re.sub(
        r"```$",
        "",
        clean
    )

    clean = clean.strip()

    print("\n==============================")
    print("INTERPRETATION CLEANED RESPONSE")
    print("==============================")
    print(clean)
    print("==============================\n")

    # return json.loads(clean)

    parsed = json.loads(clean)

    return {
        "interpretations": parsed.get(
            "interpretations",
            []
        )
    }
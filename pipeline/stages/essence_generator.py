from anthropic import AsyncAnthropic
import os
import json

client = AsyncAnthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY")
)

# PROMPT = """
# You are a Narrative Essence Generator.

# Convert semantic inspiration into:

# - narrative essences
# - emotional abstractions
# - symbolic meanings

# Return JSON:

# {
#  "essences":[]
# }
# """


PROMPT = """
You are a Narrative Essence Generator.

Input:
Semantic inspiration.

Extract 10-25 concise narrative essences.

Rules:

- 2-6 words each
- abstract
- emotionally meaningful
- editable by humans
- no explanations
- no metadata
- no IDs
- no nested objects

Return JSON ONLY:

{
  "essences": [
    "Isolation",
    "Urban Stillness",
    "Emotional Distance"
  ]
}
"""



async def generate_essences(
    semantic_inspiration: dict
):

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        temperature=0.7,
        messages=[
            {
                "role":"user",
                "content":
                PROMPT +
                "\n\n" +
                json.dumps(semantic_inspiration)
            }
        ]
    )

    # return json.loads(
    #     response.content[0].text
    # )
    raw = response.content[0].text

    print("\n==============================")
    print("ESSENCE RAW RESPONSE")
    print("==============================")
    print(raw)
    print("==============================\n")

    import re

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
    print("ESSENCE CLEANED RESPONSE")
    print("==============================")
    print(clean)
    print("==============================\n")

    # return json.loads(clean)
    parsed = json.loads(clean)

    return {
        "essences": parsed.get("essences", [])
    }
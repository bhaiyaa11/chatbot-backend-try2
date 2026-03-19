import json
import os
import asyncio
import logging
import vertexai
from google.cloud import aiplatform
from supabase import create_client
from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger(__name__)
MIN_EXAMPLES_FOR_TUNING = 100   # don't tune until you have enough data


async def export_training_jsonl() -> str:
    """
    Pull good scripts from Supabase, convert to Vertex JSONL format.
    Returns path to the exported file.
    """
    supabase_client = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )

    result = supabase_client.table("training_data") \
        .select("prompt, output") \
        .eq("rating", 1) \
        .eq("used_in_training", False) \
        .execute()

    if len(result.data) < MIN_EXAMPLES_FOR_TUNING:
        logger.info(f"Only {len(result.data)} examples — need {MIN_EXAMPLES_FOR_TUNING} to fine-tune")
        return None

    # Convert to Vertex AI JSONL format
    path = "/tmp/training_data.jsonl"
    with open(path, "w") as f:
        for row in result.data:
            record = {
                "input_text": row["prompt"],
                "output_text": row["output"],
            }
            f.write(json.dumps(record) + "\n")

    logger.info(f"Exported {len(result.data)} examples to {path}")
    return path


async def trigger_fine_tune_job(training_file_path: str):
    """
    Upload training data to GCS and start a Vertex AI fine-tune job.
    """
    from google.cloud import storage

    # Upload JSONL to GCS
    bucket_name = os.getenv("GCS_BUCKET_NAME") or os.environ["GCS_BUCKET_NAME"]
    gcs_path = "training/training_data.jsonl"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(training_file_path)

    gcs_uri = f"gs://{bucket_name}/{gcs_path}"
    logger.info(f"Uploaded training data to {gcs_uri}")

    # Start Vertex AI tuning job
    aiplatform.init(project="poc-script-genai", location="us-central1")

    job = aiplatform.CustomJob.from_local_script(
        display_name="script-model-fine-tune",
        script_path="fine_tune_runner.py",   # your fine-tune script
        container_uri="us-docker.pkg.dev/vertex-ai/training/pytorch-gpu.1-13:latest",
        args=[f"--training_data={gcs_uri}"],
    )

    job.run(sync=False)   # non-blocking
    logger.info(f"Fine-tune job started: {job.display_name}")
    return job.display_name


async def mark_examples_as_used(supabase_client):
    """Mark all rated examples as used so we don't retrain on them."""
    supabase_client.table("training_data") \
        .update({"used_in_training": True}) \
        .eq("rating", 1) \
        .eq("used_in_training", False) \
        .execute()
    

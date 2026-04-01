import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

def check_db():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    supabase = create_client(url, key)
    
    print(f"Checking {url}...")
    
    try:
        res = supabase.table("scripts").select("count").execute()
        print(f"✅ 'scripts' table exists. Count: {res.data}")
    except Exception as e:
        print(f"❌ 'scripts' table check failed: {e}")

    try:
        res = supabase.table("script_chunks").select("count").execute()
        print(f"✅ 'script_chunks' table exists. Count: {res.data}")
    except Exception as e:
        print(f"❌ 'script_chunks' table check failed: {e}")

    try:
        # Try a dummy RPC call to check if match_chunks exists
        res = supabase.rpc("match_chunks", {
            "query_embedding": [0.0]*768,
            "match_threshold": 0.5,
            "match_count": 1,
            "metadata_filter": {}
        }).execute()
        print("✅ 'match_chunks' RPC exists.")
    except Exception as e:
        print(f"❌ 'match_chunks' RPC check failed: {e}")

if __name__ == "__main__":
    check_db()

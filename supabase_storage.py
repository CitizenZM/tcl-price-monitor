"""
Supabase-backed storage for TCL Price Monitor baseline data.
Provides persistent storage for Vercel deployments.
Falls back to local JSON storage if Supabase is unavailable.
"""

import os
import json
from pathlib import Path
from datetime import datetime

# Try to import Supabase
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False
    print("[WARN] supabase not installed. Using local file storage only.")


class BaselineStorage:
    """Handle baseline storage with Supabase fallback."""

    def __init__(self, base_path, table_name="tcl_price_baseline"):
        self.base_path = Path(base_path)
        self.table_name = table_name
        self.local_path = self.base_path / 'data' / 'tcl_price_baseline.json'
        self.supabase = self._init_supabase()

    def _init_supabase(self) -> Client:
        """Initialize Supabase client if credentials available."""
        if not SUPABASE_AVAILABLE:
            return None

        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY')

        if url and key:
            try:
                return create_client(url, key)
            except Exception as e:
                print(f"[WARN] Supabase init failed: {e}. Using local storage.")
                return None
        return None

    def load(self) -> dict:
        """Load baseline from Supabase or local file."""
        if self.supabase:
            return self._load_from_supabase()
        return self._load_from_local()

    def save(self, baseline: dict) -> bool:
        """Save baseline to Supabase and local file."""
        # Always save locally as backup
        self._save_to_local(baseline)

        # Try to save to Supabase
        if self.supabase:
            return self._save_to_supabase(baseline)
        return True

    def _load_from_local(self) -> dict:
        """Load baseline from local JSON file."""
        if self.local_path.exists():
            try:
                with open(self.local_path, 'r') as f:
                    data = json.load(f)
                print(f"[OK] Loaded baseline from {self.local_path}")
                return data
            except Exception as e:
                print(f"[WARN] Failed to load local baseline: {e}")
                return {}
        return {}

    def _save_to_local(self, baseline: dict):
        """Save baseline to local JSON file."""
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.local_path, 'w') as f:
                json.dump(baseline, f, indent=2, default=str)
            print(f"[OK] Saved baseline to {self.local_path}")
        except Exception as e:
            print(f"[WARN] Failed to save local baseline: {e}")

    def _load_from_supabase(self) -> dict:
        """Load baseline from Supabase."""
        try:
            response = self.supabase.table(self.table_name).select("data").eq("id", "latest").single().execute()
            if response.data and response.data.get('data'):
                print(f"[OK] Loaded baseline from Supabase")
                return response.data['data']
        except Exception as e:
            print(f"[WARN] Supabase load failed: {e}. Falling back to local storage.")

        # Fallback to local
        return self._load_from_local()

    def _save_to_supabase(self, baseline: dict) -> bool:
        """Save baseline to Supabase."""
        try:
            record = {
                "id": "latest",
                "data": baseline,
                "updated_at": datetime.utcnow().isoformat(),
            }

            # Try to update existing record
            try:
                self.supabase.table(self.table_name).update(record).eq("id", "latest").execute()
                print(f"[OK] Updated baseline in Supabase")
                return True
            except:
                # If update fails, try insert
                self.supabase.table(self.table_name).insert(record).execute()
                print(f"[OK] Inserted baseline into Supabase")
                return True

        except Exception as e:
            print(f"[WARN] Supabase save failed: {e}. Using local storage only.")
            return False


def setup_supabase_table(supabase_url: str, supabase_key: str) -> bool:
    """
    Create the baseline table in Supabase if it doesn't exist.
    Run this once during setup.

    Table schema:
    - id (text, primary key): "latest"
    - data (jsonb): Full baseline dictionary
    - updated_at (timestamp): Last update time
    """
    try:
        client = create_client(supabase_url, supabase_key)

        # Note: In production, create the table manually in Supabase dashboard
        # This is just documentation of the expected schema
        print("[INFO] Baseline table schema:")
        print("  id: text (primary key)")
        print("  data: jsonb")
        print("  updated_at: timestamp")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to setup Supabase: {e}")
        return False

"""Embed all leaflet chunks once: python scripts/build_index.py (needs GEMINI_API_KEY)."""

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from strip_reader.leaflets import build_index  # noqa: E402

print(f"indexed {build_index()} chunks")

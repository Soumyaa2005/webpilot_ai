import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Tests are strict by default; only the e2e fixture opts into the relaxed mode.
os.environ.pop("WEBPILOT_ALLOW_PRIVATE_TEST", None)

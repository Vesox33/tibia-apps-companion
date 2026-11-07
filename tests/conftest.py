import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("TIBIA_HUNT_UPLOADER_HOME", tempfile.mkdtemp(prefix="thu-test-"))

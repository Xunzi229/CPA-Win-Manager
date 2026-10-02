"""Application paths independent of package location and working directory."""
from pathlib import Path
import sys

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2])
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))

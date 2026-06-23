import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from toolbox.app import main


if __name__ == "__main__":
    raise SystemExit(main())

"""Run recent from a clone, from any folder: python path\\to\\win-utils\\recent.py ROOT"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from winutils.recent.cli import main  # noqa: E402

raise SystemExit(main())

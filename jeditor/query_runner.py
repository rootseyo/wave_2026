#!/usr/bin/env python3
"""Compatibility entry point; use step2_query_runner.py."""
import warnings

# Suppress terminal library warnings
warnings.filterwarnings("ignore")
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

from step2_query_runner import *

if __name__ == "__main__":
    main()

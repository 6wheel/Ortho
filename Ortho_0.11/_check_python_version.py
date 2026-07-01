"""Tiny standalone helper used by Start App.bat to check the Python
version meets this app's minimum requirement (3.9+).

This is a separate file, rather than a one-line Python command embedded
directly in the .bat file, specifically to avoid a real bug found via
actual user testing: a Python one-liner containing parentheses (e.g.
"sys.version_info >= (3, 9)"), embedded in a quoted string, immediately
followed by a batch "if (...)" block on the next line, crashes cmd.exe's
parser with ". was unexpected at this time." -- cmd.exe's bracket-balance
tracking operates on the raw line text and gets confused by parentheses
inside what is, to it, just an opaque quoted string. Keeping this check
in its own .py file sidesteps the whole class of problem rather than
trying to carefully avoid every parenthesis-adjacency edge case inline.

Exit code 0 = Python is new enough. Exit code 1 = too old.
"""
import sys

if sys.version_info.major == 3 and sys.version_info.minor >= 9:
    sys.exit(0)
elif sys.version_info.major > 3:
    sys.exit(0)
else:
    sys.exit(1)

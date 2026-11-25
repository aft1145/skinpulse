import sys

from skinpulse.cli import app, tolerate_console_encoding

tolerate_console_encoding(sys.stdout, sys.stderr)
app()

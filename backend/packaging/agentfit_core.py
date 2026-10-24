"""Entry point of the frozen Will It Run core (willitrun-core.exe).

The desktop shell starts it with ``serve``; from a terminal it is the full ``agentfit`` CLI.
"""

import multiprocessing

from agentfit.cli import app

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app()

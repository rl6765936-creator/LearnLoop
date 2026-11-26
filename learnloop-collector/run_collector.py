"""Entry point used to build the .exe (Stage 8).

Double-clicking the .exe (no command) shows the status and the list of commands,
then waits for Enter so the window does not close immediately.
"""

import sys

from collector.main import main

if __name__ == "__main__":
    if len(sys.argv) == 1 and getattr(sys, "frozen", False):
        main(["status"])
        print()
        print(
            "Commands (run from a terminal):  learnloop-collector.exe "
            "grant | status | scan | watch | read FILE | review | outbox | revoke | pause | resume")
        input("\nPress Enter to close...")
    else:
        main()

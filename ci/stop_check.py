#!/usr/bin/env python3
"""Claude Code Stop hook: an agent may finish only when `python ci/run.py` exits 0.

Install: copy ci/claude-settings.json to .claude/settings.json (or merge its "hooks" into yours).
Red: exit 2, and the check's last lines go back to the agent, which keeps working. After 4 blocks in a row it lets
the agent stop anyway, so a stuck model cannot loop for ever. The check, not the agent's word, decides green.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUNT = ROOT / ".venv" / "stop-check-blocks.txt"  # in the repo's own .venv: ignored by git, never in a shared folder
MAX_BLOCKS = 4


def main():
    run = subprocess.run([sys.executable, str(ROOT / "ci" / "run.py")], cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace")
    text = COUNT.read_text().strip() if COUNT.exists() else ""
    blocks = (int(text) if text.isdigit() else 0) + 1
    if run.returncode == 0 or blocks > MAX_BLOCKS:
        COUNT.unlink(missing_ok=True)
        return 0
    COUNT.parent.mkdir(exist_ok=True)
    COUNT.write_text(str(blocks))
    tail = "\n".join(run.stdout.splitlines()[-40:])
    print(f"Not done yet: `python ci/run.py` exited with code {run.returncode}. Its last lines:\n{tail}\n"
          "Fix the cause, then finish.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

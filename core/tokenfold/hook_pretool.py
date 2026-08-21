"""Claude Code PreToolUse hook: measure (and optionally compress) Agent/Task
prompts. Reads the hook payload on stdin, writes JSON on stdout.

Passthrough-safe: on ANY error it emits {} (no-op). Compression of the prompt
is only applied when TOKENFOLD_HOOK_COMPRESS=1 is set, because rewriting tool
input is an intrusive action; by default the hook only records metrics.
"""

import json
import os
import sys


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        prompt = (payload.get("tool_input") or {}).get("prompt", "")
        if not prompt or len(prompt) < 200:
            print("{}")
            return
        from tokenfold.engine import Engine
        eng = Engine()
        msgs, rep = eng.encode([{"role": "user", "content": prompt}], "claude")
        if (os.environ.get("TOKENFOLD_HOOK_COMPRESS") == "1"
                and not rep.fallback and rep.saved >= 20):
            new_input = dict(payload.get("tool_input") or {})
            new_input["prompt"] = msgs[-1]["content"]
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": new_input,
                }}))
        else:
            print("{}")
    except Exception:
        print("{}")


if __name__ == "__main__":
    main()

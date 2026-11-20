"""TokenFold CLI.

  tokenfold serve [--port 9339] [--upstream URL]   run the proxy
  tokenfold stats                                   lifetime savings summary
  tokenfold bench                                   run the benchmark suite
  tokenfold encode "text" [--model MODEL]           one-shot encode preview
  tokenfold dict [list|export|import FILE|mint]     dictionary management
  tokenfold config [get|set KEY VALUE]              configuration
"""

from __future__ import annotations

import argparse
import json
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tokenfold")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve")
    sp.add_argument("--port", type=int, default=9339)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--upstream", default=None)

    sub.add_parser("stats")
    sub.add_parser("bench")

    ep = sub.add_parser("encode")
    ep.add_argument("text")
    ep.add_argument("--model", default="gpt-4o")

    dp = sub.add_parser("dict")
    dp.add_argument("action", choices=["list", "export", "import", "mint"],
                    nargs="?", default="list")
    dp.add_argument("file", nargs="?")

    cp = sub.add_parser("config")
    cp.add_argument("action", choices=["get", "set"])
    cp.add_argument("key", nargs="?")
    cp.add_argument("value", nargs="?")
    return p


def _handle_serve(a: argparse.Namespace) -> int:
    import uvicorn
    from .adapters.proxy import create_app
    from .engine import Engine
    eng = Engine()
    if a.upstream:
        eng.cfg.upstream = a.upstream
    print(f"TokenFold proxy on http://{a.host}:{a.port}/v1 "
          f"-> upstream {eng.cfg.upstream}  (mode={eng.cfg.mode})")
    print(f"dashboard: http://{a.host}:{a.port}/tokenfold/dashboard")
    uvicorn.run(create_app(eng), host=a.host, port=a.port, log_level="warning")
    return 0


def _handle_stats(a: argparse.Namespace) -> int:
    from .core.metrics import Metrics
    print(Metrics().dump_json())
    return 0


def _handle_bench(a: argparse.Namespace) -> int:
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    return subprocess.call([sys.executable, str(root / "benchmarks" / "run_phase1.py")])


def _handle_encode(a: argparse.Namespace) -> int:
    from .engine import Engine
    eng = Engine()
    msgs, rep = eng.encode([{"role": "user", "content": a.text}], a.model)
    print(json.dumps({"encoded": msgs, "original_tokens": rep.original_tokens,
                      "encoded_tokens": rep.encoded_tokens,
                      "dictionary_overhead": rep.dictionary_overhead,
                      "saved": rep.saved, "pct": round(rep.pct, 1),
                      "latency_ms": round(rep.latency_ms, 2),
                      "fallback": rep.fallback},
                     indent=1, ensure_ascii=False))
    return 0


def _handle_dict(a: argparse.Namespace) -> int:
    from .core.dictionary import Dictionary
    d = Dictionary()
    if a.action == "list":
        for c, al in sorted(d.aliases.items()):
            print(f"{c:5s} = {al.expansion}  (uses={al.uses})")
        gen = d.active_generation
        print(f"\ngeneration: {gen.version if gen else 0}; "
              f"nursery: {len(d.nursery)} candidates")
    elif a.action == "export":
        print(d.export_json())
    elif a.action == "import" and a.file:
        d.import_json(open(a.file, encoding="utf-8").read())
        print("imported")
    elif a.action == "mint":
        gen = d.mint()
        print(f"minted generation {gen.version}" if gen
              else "nothing promotable (net savings would not clear the bar)")
    return 0


def _handle_config(a: argparse.Namespace) -> int:
    from .core import config as cfgmod
    cfg = cfgmod.load()
    if a.action == "get":
        print(json.dumps(cfg.__dict__, indent=1))
    else:
        if not a.key or a.value is None:
            print("usage: tokenfold config set KEY VALUE")
            return 2
        cur = getattr(cfg, a.key, None)
        if cur is None:
            print(f"unknown key {a.key}")
            return 2
        typ = type(cur)
        val = (a.value.lower() in ("1", "true", "yes")) if typ is bool \
            else typ(a.value)
        setattr(cfg, a.key, val)
        cfgmod.save(cfg.clamp())
        print(f"{a.key} = {val}")
    return 0


_HANDLERS = {
    "serve": _handle_serve,
    "stats": _handle_stats,
    "bench": _handle_bench,
    "encode": _handle_encode,
    "dict": _handle_dict,
    "config": _handle_config,
}


def main(argv: list[str] | None = None) -> int:
    # under pythonw.exe there is no console: make print() a safe no-op
    if sys.stdout is None or sys.stderr is None:
        import io
        sys.stdout = sys.stdout or io.StringIO()
        sys.stderr = sys.stderr or io.StringIO()
    a = _build_parser().parse_args(argv)
    handler = _HANDLERS.get(a.cmd)
    if handler is None:
        return 1
    return handler(a)


if __name__ == "__main__":
    sys.exit(main())

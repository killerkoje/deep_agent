"""Drive the graph against real models, printing each step as it lands.

Bypasses HTTP on purpose. A real run takes longer than any sensible
request timeout, and the first live run is the one where you most need
to watch what the model actually does rather than wait for a summary.

    uv run python scripts/live_run.py examples/prd-crm-metrics.md --feature crm
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from deep_agent.graph import build_graph  # noqa: E402
from deep_agent.llm import text_of  # noqa: E402
from deep_agent.state import initial_state  # noqa: E402


def describe(msg) -> str | None:
    if isinstance(msg, AIMessage):
        if msg.tool_calls:
            return "  ".join(
                f"-> {c['name']}({_args(c['args'])})" for c in msg.tool_calls
            )
        return f"   say: {text_of(msg)[:160]}"
    if isinstance(msg, ToolMessage):
        head = str(msg.content).strip().splitlines()[0] if msg.content else ""
        mark = "!! " if "GateReject" in str(msg.content) else "   "
        return f"{mark}<- {head[:160]}"
    return None


def _args(args: dict) -> str:
    bits = []
    for k in ("skill", "model", "root_cause", "status"):
        if k in args:
            bits.append(f"{k}={args[k]}")
    if "brief" in args:
        bits.append(f"brief={str(args['brief'])[:50]!r}")
    if "items" in args:
        bits.append(f"{len(args['items'])} todos")
    return ", ".join(bits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--feature", default="demo")
    ap.add_argument("--max-steps", type=int, default=60)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    sources = {Path(p).name: Path(p).read_text(encoding="utf-8") for p in args.sources}
    thread_id = f"live_{int(time.time())}_{args.feature}"

    state = initial_state(
        thread_id=thread_id, feature_id=args.feature, sources=sources
    )
    state["sdd"]["last_event"] = "sources.ready"
    state["messages"] = [HumanMessage(f"Feature: {args.feature}. Begin.")]

    graph = build_graph()
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": args.max_steps}

    print(f"thread  {thread_id}")
    print(f"sources {', '.join(sources)}\n")

    seen = 0
    started = time.time()
    try:
        for chunk in graph.stream(state, cfg, stream_mode="values"):
            msgs = chunk.get("messages") or []
            for m in msgs[seen:]:
                if line := describe(m):
                    print(f"[{time.time() - started:5.0f}s] {line}", flush=True)
            seen = len(msgs)
    except KeyboardInterrupt:
        print("\ninterrupted by user")
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}")

    snap = graph.get_state(cfg)
    v, sdd = snap.values, (snap.values.get("sdd") or {})

    print("\n" + "=" * 62)
    print(f"status          {v.get('status')}   stage {sdd.get('stage')}")
    print(f"verify_passed   {sdd.get('verify_passed')}")
    print(f"ready_open      {sdd.get('ready_open_count')}")
    print(f"spawns          {len(sdd.get('spawn_history') or [])}")
    print(f"decisions       {len(sdd.get('decisions') or [])}")
    print(f"spent           ${(sdd.get('budget') or {}).get('spent_usd', 0):.4f}")
    print(f"files           {', '.join(sorted(v.get('files') or {}))}")

    if snap.interrupts:
        print("\nPARKED on the human gate:")
        for i in snap.interrupts:
            for item in (i.value or {}).get("items", []):
                print(f"  [{item.get('id')}] {item.get('category')} {item.get('title')}")

    if rej := sdd.get("gate_rejects"):
        print("\ngate rejections:")
        for r in rej:
            print(f"  {r.get('code')} ({r.get('skill')}) {r.get('message', '')[:90]}")

    print("\nspawns:")
    for h in sdd.get("spawn_history") or []:
        print(
            f"  {h['skill']:16} {h['model']:14} "
            f"{h.get('tokens_in', 0):>7}/{h.get('tokens_out', 0):<7} "
            f"${h.get('cost_usd', 0):.4f}"
        )

    out = Path("workspace") / thread_id
    out.mkdir(parents=True, exist_ok=True)
    for path, content in (v.get("files") or {}).items():
        f = out / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    print(f"\nartifacts written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line client.

A thin wrapper over the HTTP API - it never imports the graph. If the
CLI drove the graph directly there would be two execution paths to keep
honest, and the one people actually use in a terminal would drift from
the one the server runs.

    deep-agent run --feature crm docs/prd.md docs/mail.md
    deep-agent status <thread>
    deep-agent gate <thread>
    deep-agent gate <thread> -a D-013="① 지점명 마스킹"
    deep-agent files <thread> [path]
    deep-agent report <thread>
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = os.getenv("ORCH_URL", "http://127.0.0.1:8000")


# --- output ----------------------------------------------------------
# Windows terminals are frequently not UTF-8; the artifacts are Korean.
# Reconfigure rather than let the first 결정 crash the process.

def _setup_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


C = {
    "dim": "\033[2m", "red": "\033[31m", "green": "\033[32m",
    "yellow": "\033[33m", "bold": "\033[1m", "off": "\033[0m",
}
if os.getenv("NO_COLOR") or not sys.stdout.isatty():
    C = dict.fromkeys(C, "")


def say(msg: str = "") -> None:
    print(msg)


def die(msg: str, code: int = 1) -> None:
    print(f"{C['red']}error:{C['off']} {msg}", file=sys.stderr)
    raise SystemExit(code)


# --- transport --------------------------------------------------------


class Api:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.http = httpx.Client(
            base_url=self.base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=600.0,  # a run can spend minutes inside one request
        )

    def __call__(self, method: str, path: str, **kw) -> Any:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.ConnectError:
            die(f"no server at {self.base}\n  start one: uv run uvicorn deep_agent.app:app")
        if r.status_code == 401:
            die("401 - wrong token. set ORCH_API_TOKEN or pass --token")
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text) if "json" in r.headers.get(
                "content-type", ""
            ) else r.text
            die(f"{r.status_code} {detail}")
        return r.json()


# --- rendering --------------------------------------------------------


def show_status(body: dict) -> None:
    ok = C["green"] if body.get("verify_passed") else C["dim"]
    say(f"{C['bold']}{body.get('thread_id', '')}{C['off']}")
    say(f"  stage          {body.get('stage')}")
    say(f"  status         {body.get('status')}")
    say(f"  verify_passed  {ok}{body.get('verify_passed')}{C['off']}")

    open_count = body.get("ready_open_count")
    if open_count is not None:
        tint = C["green"] if open_count == 0 else C["yellow"]
        say(f"  ready open     {tint}{open_count}{C['off']}")

    say(f"  spawns         {body.get('spawns')}   spent ${body.get('spent_usd', 0):.4f}")

    if body.get("waiting_on_human"):
        say(f"\n  {C['yellow']}waiting for a person{C['off']} - see: deep-agent gate <thread>")

    if todos := body.get("todos"):
        say("\n  plan")
        mark = {"done": "x", "doing": ">", "pending": " "}
        for t in todos:
            say(f"    [{mark.get(t['status'], ' ')}] {t['id']} {t['text']}")

    if files := body.get("files"):
        say(f"\n  files          {', '.join(files)}")


def show_gate(body: dict) -> None:
    items = body.get("items") or []
    if not items:
        say(f"{C['green']}nothing waiting.{C['off']}")
        return

    say(f"{C['yellow']}{len(items)} decision(s) need a person.{C['off']}")
    say(f"{C['dim']}Only permission/security and money/billing stop the run -")
    say(f"getting these wrong cannot be undone by a later code change.{C['off']}\n")

    for item in items:
        say(f"{C['bold']}[{item['id']}]{C['off']} {item.get('category')} — {item.get('title', '')}")
        if text := item.get("text"):
            say(f"  {text}")
        if options := item.get("options"):
            say(f"  options: {options}")
        say(f"  {C['dim']}answer: deep-agent gate <thread> -a {item['id']}=\"...\"{C['off']}\n")


# --- commands ---------------------------------------------------------


def cmd_run(api: Api, args) -> None:
    sources: dict[str, str] = {}
    for raw in args.sources:
        p = Path(raw)
        if not p.is_file():
            die(f"no such file: {p}")
        sources[p.name] = p.read_text(encoding="utf-8")

    say(f"{C['dim']}submitting {len(sources)} source file(s)...{C['off']}")
    body = api(
        "POST",
        "/api/v1/threads",
        json={
            "feature_id": args.feature,
            "sources": sources,
            "target_repo_path": args.repo,
            "max_iterations": args.max_iterations,
        },
    )
    show_status(body)


def cmd_status(api: Api, args) -> None:
    show_status({"thread_id": args.thread, **api("GET", f"/api/v1/threads/{args.thread}")})


def cmd_gate(api: Api, args) -> None:
    path = f"/api/v1/threads/{args.thread}/human-gate"
    if not args.answer:
        show_gate(api("GET", path))
        return

    answers = []
    for pair in args.answer:
        if "=" not in pair:
            die(f"expected ID=answer, got {pair!r}")
        item_id, choice = pair.split("=", 1)
        answers.append({"item_id": item_id.strip(), "choice": choice.strip()})

    body = api("POST", path, json={"answers": answers})
    say(f"{C['green']}recorded {len(answers)} answer(s); run resumed.{C['off']}\n")
    show_status({"thread_id": args.thread, **body})


def cmd_files(api: Api, args) -> None:
    if not args.path:
        for name in api("GET", f"/api/v1/threads/{args.thread}")["files"]:
            say(name)
        return
    say(api("GET", f"/api/v1/threads/{args.thread}/files/{args.path}")["content"])


def cmd_report(api: Api, args) -> None:
    say(api("GET", f"/api/v1/threads/{args.thread}/report")["report"])


def cmd_models(api: Api, args) -> None:
    for m in api("GET", "/api/v1/models")["models"]:
        price = ""
        if m.get("input_per_1m") is not None:
            price = f"  ${m['input_per_1m']}/${m.get('output_per_1m')} per 1M"
        say(f"{C['bold']}{m['id']}{C['off']}{price}")
        say(f"  {C['dim']}{m.get('notes', '')}{C['off']}\n")


def cmd_history(api: Api, args) -> None:
    for c in api("GET", f"/api/v1/threads/{args.thread}/history")["checkpoints"]:
        nxt = ",".join(c["next"]) or "-"
        say(f"{c['checkpoint_id'][:8]}  {str(c['stage'] or '-'):16} next={nxt}")


# --- wiring -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deep-agent", description=__doc__.split("\n")[0])
    p.add_argument("--url", default=DEFAULT_BASE, help=f"API base (default {DEFAULT_BASE})")
    p.add_argument("--token", default=os.getenv("ORCH_API_TOKEN", "change-me"))
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start a run from source documents")
    r.add_argument("sources", nargs="+", help="PRD, mails, prior specs")
    r.add_argument("--feature", required=True)
    r.add_argument("--repo", default=None, help="target repo path for implement/qa")
    r.add_argument("--max-iterations", type=int, default=3, dest="max_iterations")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="where a run is")
    s.add_argument("thread")
    s.set_defaults(fn=cmd_status)

    g = sub.add_parser("gate", help="show or answer permission/billing questions")
    g.add_argument("thread")
    g.add_argument("-a", "--answer", action="append", metavar="ID=CHOICE")
    g.set_defaults(fn=cmd_gate)

    f = sub.add_parser("files", help="list artifacts, or print one")
    f.add_argument("thread")
    f.add_argument("path", nargs="?")
    f.set_defaults(fn=cmd_files)

    rep = sub.add_parser("report", help="print the total report")
    rep.add_argument("thread")
    rep.set_defaults(fn=cmd_report)

    m = sub.add_parser("models", help="what Main may choose from")
    m.set_defaults(fn=cmd_models)

    h = sub.add_parser("history", help="checkpoint list (debugging)")
    h.add_argument("thread")
    h.set_defaults(fn=cmd_history)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    args = build_parser().parse_args(argv)
    args.fn(Api(args.url, args.token), args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

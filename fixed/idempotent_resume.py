import asyncio
import json
from pathlib import Path
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import TimeoutPolicy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
LEDGER = RESULTS / "idempotency-ledger.json"
EFFECT_LOG = RESULTS / "idempotent-effects.jsonl"


class State(TypedDict, total=False):
    status: str


def load_ledger() -> dict:
    if not LEDGER.exists():
        return {}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def commit_once(operation_id: str) -> bool:
    ledger = load_ledger()
    if ledger.get(operation_id) == "committed":
        return False
    EFFECT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EFFECT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"operation_id": operation_id}) + "\n")
    ledger[operation_id] = "committed"
    LEDGER.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return True


async def main() -> dict:
    LEDGER.unlink(missing_ok=True)
    EFFECT_LOG.unlink(missing_ok=True)
    attempts = {"value": 0}

    async def tool(state: State):
        attempts["value"] += 1
        created = commit_once("charge-001")
        if created:
            await asyncio.sleep(0.20)
        return {"status": "ok"}

    builder = StateGraph(State)
    builder.add_node("tool", tool, timeout=TimeoutPolicy(run_timeout=0.05))
    builder.add_edge(START, "tool")
    builder.add_edge("tool", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "timeout-idempotent"}}

    first_error = None
    try:
        await graph.ainvoke({"status": "start"}, config)
    except Exception as exc:
        first_error = type(exc).__name__

    resumed = await graph.ainvoke(None, config)
    state = await graph.aget_state(config)
    effects = [json.loads(line) for line in EFFECT_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = {
        "first_error": first_error,
        "attempts": attempts["value"],
        "effect_count": len(effects),
        "resumed_result": resumed,
        "checkpoint_next": list(state.next),
        "contained": first_error == "NodeTimeoutError" and attempts["value"] == 2 and len(effects) == 1 and resumed.get("status") == "ok" and not state.next,
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(main())

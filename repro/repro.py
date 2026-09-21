import asyncio
import json
from pathlib import Path
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import TimeoutPolicy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
EFFECT_LOG = RESULTS / "baseline-effects.jsonl"


class State(TypedDict, total=False):
    status: str


def append_effect(attempt: int) -> None:
    EFFECT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EFFECT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"operation_id": "charge-001", "attempt": attempt}) + "\n")


async def main() -> dict:
    EFFECT_LOG.unlink(missing_ok=True)
    attempt = {"value": 0}

    async def tool(state: State):
        attempt["value"] += 1
        append_effect(attempt["value"])
        await asyncio.sleep(0.20)
        return {"status": "ok"}

    builder = StateGraph(State)
    builder.add_node("tool", tool, timeout=TimeoutPolicy(run_timeout=0.05))
    builder.add_edge(START, "tool")
    builder.add_edge("tool", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "timeout-baseline"}}

    errors = []
    for input_value in ({"status": "start"}, None):
        try:
            await graph.ainvoke(input_value, config)
        except Exception as exc:
            errors.append(type(exc).__name__)

    state = await graph.aget_state(config)
    effects = [json.loads(line) for line in EFFECT_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = {
        "errors": errors,
        "effect_count": len(effects),
        "effects": effects,
        "checkpoint_values": dict(state.values),
        "checkpoint_next": list(state.next),
        "reproduced": errors == ["NodeTimeoutError", "NodeTimeoutError"] and len(effects) == 2 and list(state.next) == ["tool"],
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(main())

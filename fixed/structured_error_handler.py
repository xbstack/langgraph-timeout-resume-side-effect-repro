import asyncio
import json
from pathlib import Path
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import TimeoutPolicy

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
EFFECT_LOG = RESULTS / "handler-effects.jsonl"


class State(TypedDict, total=False):
    status: str
    error_kind: str


async def main() -> dict:
    EFFECT_LOG.unlink(missing_ok=True)

    async def tool(state: State):
        EFFECT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EFFECT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"operation_id": "charge-001"}) + "\n")
        await asyncio.sleep(0.20)
        return {"status": "ok"}

    def handle_timeout(state: State, error: NodeError):
        return {
            "status": "tool_failed",
            "error_kind": type(error.error).__name__,
        }

    builder = StateGraph(State)
    builder.add_node(
        "tool",
        tool,
        timeout=TimeoutPolicy(run_timeout=0.05),
        error_handler=handle_timeout,
    )
    builder.add_edge(START, "tool")
    builder.add_edge("tool", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "timeout-handler"}}

    result_state = await graph.ainvoke({"status": "start"}, config)
    state = await graph.aget_state(config)
    effects = [json.loads(line) for line in EFFECT_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = {
        "result": result_state,
        "effect_count": len(effects),
        "checkpoint_next": list(state.next),
        "structured": result_state.get("status") == "tool_failed" and result_state.get("error_kind") == "NodeTimeoutError" and len(effects) == 1 and not state.next,
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(main())

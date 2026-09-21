# LangGraph timeout resume external side-effect repro

Independent XBSTACK reproduction for the recovery boundary discussed in LangGraph issue #9006.

## Question

When an async LangGraph node times out after an external side effect has already happened, what does a same-thread resume do, and what application controls are needed to make recovery safe?

This repository tests one narrow case only:

- Python 3.12
- `langgraph==1.2.11`
- `InMemorySaver`
- async `StateGraph` node
- `TimeoutPolicy(run_timeout=0.05)`
- deterministic local side effect written before the timeout
- no model, provider API, network call, or credential

It does **not** model worker-process death, LangGraph Cloud, distributed leases, or every saver implementation.

## Observed baseline

The baseline node writes an external effect, then sleeps beyond its 50 ms run timeout.

1. Initial `ainvoke(...)` raises `NodeTimeoutError`.
2. The checkpoint remains before the timed-out node and reports that node as next.
3. `ainvoke(None, same_config)` re-executes the same node.
4. The external effect happens a second time.

Expected baseline marker:

```text
"reproduced": true
```

The important boundary is not that LangGraph duplicates arbitrary external systems by itself. The graph correctly discards buffered graph writes from the timed-out attempt; the duplicate comes from **re-executing application code whose external side effect was not idempotent**.

## Control 1: stable idempotency key

`fixed/idempotent_resume.py` writes the external operation under a stable operation key before the timeout.

On resume, the node runs again but detects that `charge-001` is already committed and returns without repeating the effect.

Expected marker:

```text
"contained": true
```

This is an application-level containment pattern, not an upstream LangGraph fix. A real integration needs an atomic idempotency contract at the external system or a durable operation ledger with reconciliation.

## Control 2: structured timeout state

`fixed/structured_error_handler.py` attaches a node `error_handler`.

After the timeout, the handler converts `NodeTimeoutError` into:

```json
{
  "status": "tool_failed",
  "error_kind": "NodeTimeoutError"
}
```

The graph then completes instead of leaving the timed-out node pending. This provides the "timeout -> structured failure observation -> continue/compensate" shape, but it still does not roll back an external effect that happened before cancellation.

## Run

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -r requirements.txt
.venv/bin/bash run.sh
```

Or, after installing the pinned requirement into any Python 3.12 environment:

```bash
bash run.sh
```

A successful run writes `results/verification.json` with:

```json
{
  "baseline_reproduced": true,
  "idempotent_contained": true,
  "structured_error_handler": true,
  "result": "PASS"
}
```

## Production interpretation

For this tested timeout path:

- same-thread `invoke(None, config)` is a **re-execution boundary**, not a guarantee that external side effects are exactly-once;
- `error_handler` can turn `NodeTimeoutError` into explicit graph state or a compensation route;
- external side effects still need stable idempotency keys, operation records, or transactional/reconciliation controls;
- "resume from checkpoint" and "restart the whole run" are not the only useful choices: a node-level timeout can be handled as a structured failure while the graph continues.

## Upstream references

- LangGraph issue #9006: https://github.com/langchain-ai/langgraph/issues/9006
- Fault tolerance docs: https://docs.langchain.com/oss/python/langgraph/fault-tolerance
- Checkpointer pending-writes notes: https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/README.md

## Related XBSTACK guide

- LangGraph retry / timeout / recovery: https://www.xbstack.com/en/ai/langgraph-agent-error-recovery-retry-timeout/?utm_source=github&utm_medium=referral&utm_campaign=langgraph_timeout_resume_side_effect&utm_content=repository_readme&ref=github

## License

MIT

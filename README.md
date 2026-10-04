# LangGraph timeout resume external side-effect repro

Independent XBSTACK reproduction for the recovery boundary discussed in LangGraph issues #9006 and #9185.

## Question

When a LangGraph node fails or times out after an external side effect has already happened, what does a same-thread resume do, and what application controls are needed to make recovery safe?

This repository contains two complementary local fixtures.

The original fixture uses:

- Python 3.12
- `langgraph==1.2.11`
- `InMemorySaver`
- async `StateGraph` node
- `TimeoutPolicy(run_timeout=0.05)`
- deterministic local side effect written before the timeout
- no model, provider API, network call, or credential

On 2026-10-04, XBSTACK added a second, network-shaped fixture against `langgraph==1.2.12` after issue #9185 documented the same production boundary with an HTTP payment provider. It starts a fake OpenAI-compatible endpoint and a fake payment provider on `127.0.0.1`, requires no API key, and sends no data off-machine.

This repository does **not** model worker-process death, LangGraph Cloud, distributed leases, or every saver implementation.

## Original TimeoutPolicy baseline

The baseline node writes an external effect, then sleeps beyond its 50 ms run timeout.

1. Initial `ainvoke(...)` raises `NodeTimeoutError`.
2. The checkpoint remains before the timed-out node and reports that node as next.
3. `ainvoke(None, same_config)` re-executes the same node.
4. The external effect happens a second time.

Expected baseline marker:

```text
"reproduced": true
```

The important boundary is not that LangGraph duplicates arbitrary external systems by itself. The graph discards buffered graph writes from the timed-out attempt; the duplicate comes from **re-executing application code whose external side effect was not idempotent**.

## Control 1: stable idempotency key

`fixed/idempotent_resume.py` writes the external operation under a stable operation key before the timeout.

On resume, the node runs again but detects that `charge-001` is already committed and returns without repeating the effect.

Expected marker:

```text
"contained": true
```

This is an application-level containment pattern, not an upstream LangGraph fix. A real integration needs an atomic idempotency contract at the external system or a durable operation ledger with reconciliation.

## 2026-10-04 fixture: ambiguous HTTP timeout

`repro/provider_timeout.py` mirrors issue #9185 more closely:

1. the fake model emits one `pay_invoice` Tool Call with id `call_1`;
2. the fake payment provider commits payment 1 immediately;
3. the provider delays the first HTTP response for 5 seconds;
4. the Tool client times out after 2 seconds;
5. the first graph invocation raises `TimeoutError`;
6. `invoke(None, config)` re-executes the Tool node;
7. without a business idempotency/reconciliation contract, the provider records payment 2.

The key observation is that **a timeout is an ambiguous outcome**. It proves the caller did not receive a timely response; it does not prove the remote side effect failed.

The expected 1.2.12 baseline is:

```json
{
  "first_error": "TimeoutError",
  "payments": 2,
  "reproduced": true
}
```

## 2026-10-04 control: provider reconciliation

`fixed/provider_reconciliation.py` keeps the same LangGraph replay but separates **Tool attempt count** from **business operation identity**.

The external write uses a stable operation key derived from the persistent logical Tool Call identity. Every resumed attempt first asks the provider whether that operation already committed.

In the tested fixture:

- LangGraph enters the Tool node twice;
- the first attempt commits payment 1 but times out;
- the resumed attempt finds the existing operation and reuses its result;
- the provider ledger remains at one payment.

Expected result:

```json
{
  "first_error": "TimeoutError",
  "tool_attempts": 2,
  "payments": 1,
  "contained": true
}
```

This does not prove every downstream provider supports idempotency or lookup. If the external system cannot deduplicate or query by a stable operation key, an ambiguous timeout should enter reconciliation or manual review rather than blind replay.

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

Or, after installing the pinned requirements into a compatible Python environment:

```bash
bash run.sh
```

A successful run writes `results/verification.json` with:

```json
{
  "baseline_reproduced": true,
  "idempotent_contained": true,
  "structured_error_handler": true,
  "provider_timeout_reproduced": true,
  "provider_reconciliation_contained": true,
  "result": "PASS"
}
```

## Production interpretation

For the tested recovery paths:

- same-thread `invoke(None, config)` is a **re-execution boundary**, not a guarantee that external side effects are exactly-once;
- a network timeout is an **ambiguous outcome**, not proof that the external write failed;
- `error_handler` can turn `NodeTimeoutError` into explicit graph state or a compensation route;
- external side effects still need stable logical operation IDs, idempotency keys, operation records, or transactional/reconciliation controls;
- replay identity and current authorization are separate concerns: after reconciliation proves an operation absent, high-risk systems should re-check current policy/approval before a genuinely new external dispatch;
- "resume from checkpoint" and "restart the whole run" are not the only useful choices: a node-level timeout can be handled as a structured failure while the graph continues.

## Upstream references

- LangGraph issue #9006: https://github.com/langchain-ai/langgraph/issues/9006
- LangGraph issue #9185: https://github.com/langchain-ai/langgraph/issues/9185
- Fault tolerance docs: https://docs.langchain.com/oss/python/langgraph/fault-tolerance
- Checkpointer pending-writes notes: https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/README.md

## Related XBSTACK guide

- LangGraph retry / timeout / recovery: https://www.xbstack.com/en/ai/langgraph-agent-error-recovery-retry-timeout/?utm_source=github&utm_medium=referral&utm_campaign=langgraph_timeout_resume_side_effect&utm_content=repository_readme&ref=github

## License

MIT

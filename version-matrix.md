# Version matrix

| Test date | Python | langgraph | Baseline same-thread resume | Idempotency-key control | error_handler control |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 | 3.12 | 1.2.11 | Re-executes timed-out node; external effect occurs twice across initial attempt + invoke(None) | Same node re-executes but external effect remains exactly once | NodeTimeoutError becomes structured graph state and graph completes |

This matrix covers the pinned local fixture only. It does not claim identical behavior for LangGraph Cloud, other savers, worker-process death, or later versions.

# Version matrix

| Test date | Python | langgraph | Baseline same-thread resume | Idempotency / reconciliation control | error_handler control |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 | 3.12 | 1.2.11 | Re-executes timed-out node; external effect occurs twice across initial attempt + `invoke(None)` | Same node re-executes but external effect remains exactly once | `NodeTimeoutError` becomes structured graph state and graph completes |
| 2026-10-04 | 3.10.2 | 1.2.11 | HTTP-provider fixture reproduces the same ambiguous-outcome replay: one requested payment becomes two | Stable operation identity + provider lookup contains the duplicate | Existing structured-timeout control remains a separate graph-level strategy |
| 2026-10-04 | 3.10.2 | 1.2.12 | HTTP-provider fixture reproduces GitHub #9185: first request commits then times out, `invoke(None, config)` replays the Tool node and produces payment 2 | Stable operation identity + provider reconciliation returns payment 1 on resume; two Tool attempts, one external payment | Existing structured-timeout control remains a separate graph-level strategy |

The matrix covers these pinned local fixtures only. It does not claim identical behavior for LangGraph Cloud, other saver implementations, worker-process death, or later versions. The 2026-10-04 HTTP-provider rows use the same ambiguous-outcome shape as issue #9185: the external system commits before the client timeout, so a missing response is not proof that the side effect failed.

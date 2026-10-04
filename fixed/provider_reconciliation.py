import json
import operator
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict


PAYMENTS: list[dict] = []
BY_KEY: dict[str, dict] = {}
TOOL_ATTEMPTS = 0


class Ledger(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        key = urllib.parse.unquote(self.path.split("/status/", 1)[-1])
        result = BY_KEY.get(key)
        if result is None:
            data = b"{}"
            self.send_response(404)
        else:
            data = json.dumps(result).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        operation_key = self.headers["Idempotency-Key"]
        if operation_key in BY_KEY:
            result = BY_KEY[operation_key]
        else:
            result = {
                "status": "PAID",
                "payment": len(PAYMENTS) + 1,
                "operation_key": operation_key,
            }
            PAYMENTS.append(result)
            BY_KEY[operation_key] = result

        if len(PAYMENTS) == 1 and self.headers.get("X-First-Attempt") == "1":
            time.sleep(5)
        try:
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def log_message(self, *args):
        pass


class FakeOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        after_tool = any(message.get("role") == "tool" for message in body["messages"])
        if after_tool:
            message = {"role": "assistant", "content": "Paid."}
            finish_reason = "stop"
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "pay_invoice",
                            "arguments": json.dumps({"invoice": "INV-1", "amount": "100.00"}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        data = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]


def main() -> dict:
    global TOOL_ATTEMPTS
    PAYMENTS.clear()
    BY_KEY.clear()
    TOOL_ATTEMPTS = 0

    model_server = serve(FakeOpenAI)
    ledger_server = serve(Ledger)
    model_host, model_port = model_server.server_address
    ledger_host, ledger_port = ledger_server.server_address

    model = init_chat_model(
        "openai:gpt-4o-mini",
        base_url=f"http://{model_host}:{model_port}/v1",
        api_key="placeholder",
    ).bind_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "pay_invoice",
                    "description": "Pay an invoice",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "invoice": {"type": "string"},
                            "amount": {"type": "string"},
                        },
                        "required": ["invoice", "amount"],
                    },
                },
            }
        ]
    )

    def provider_status(operation_key: str) -> str | None:
        encoded = urllib.parse.quote(operation_key, safe="")
        try:
            with urllib.request.urlopen(
                f"http://{ledger_host}:{ledger_port}/status/{encoded}", timeout=1
            ) as response:
                return response.read().decode()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def pay_once(invoice: str, amount: str, operation_key: str, first_attempt: bool) -> str:
        existing = provider_status(operation_key)
        if existing is not None:
            return existing
        request = urllib.request.Request(
            f"http://{ledger_host}:{ledger_port}/pay",
            method="POST",
            data=json.dumps({"invoice": invoice, "amount": amount}).encode(),
            headers={
                "Idempotency-Key": operation_key,
                "X-First-Attempt": "1" if first_attempt else "0",
            },
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.read().decode()

    def llm_call(state: MessagesState):
        return {
            "messages": [
                model.invoke([SystemMessage(content="Pay the invoice once.")] + state["messages"])
            ]
        }

    def tool_node(state: MessagesState):
        global TOOL_ATTEMPTS
        TOOL_ATTEMPTS += 1
        outputs = []
        for tool_call in state["messages"][-1].tool_calls:
            operation_key = f"provider-timeout:{tool_call['id']}"
            content = pay_once(
                tool_call["args"]["invoice"],
                tool_call["args"]["amount"],
                operation_key,
                TOOL_ATTEMPTS == 1,
            )
            outputs.append(ToolMessage(content=content, tool_call_id=tool_call["id"]))
        return {"messages": outputs}

    def should_continue(state: MessagesState) -> Literal["tool_node", END]:
        return "tool_node" if state["messages"][-1].tool_calls else END

    builder = StateGraph(MessagesState)
    builder.add_node("llm_call", llm_call)
    builder.add_node("tool_node", tool_node)
    builder.add_edge(START, "llm_call")
    builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
    builder.add_edge("tool_node", "llm_call")
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "provider-timeout-fixed"}}

    first_error = None
    resumed_tool_result = None
    try:
        graph.invoke(
            {"messages": [HumanMessage(content="Pay invoice INV-1 for 100.00.")]},
            config,
        )
    except Exception as exc:
        first_error = type(exc).__name__
        resumed = graph.invoke(None, config)
        resumed_tool_result = resumed["messages"][-2].content

    result = {
        "langgraph": version("langgraph"),
        "first_error": first_error,
        "tool_attempts": TOOL_ATTEMPTS,
        "payments": len(PAYMENTS),
        "resumed_tool_result": resumed_tool_result,
        "contained": (
            first_error == "TimeoutError"
            and TOOL_ATTEMPTS == 2
            and len(PAYMENTS) == 1
            and "\"payment\": 1" in (resumed_tool_result or "")
        ),
    }
    print(json.dumps(result, indent=2))

    model_server.shutdown()
    ledger_server.shutdown()
    model_server.server_close()
    ledger_server.server_close()
    return result


if __name__ == "__main__":
    main()

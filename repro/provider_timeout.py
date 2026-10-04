import json
import operator
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import Annotated, TypedDict


PAYMENTS: list[float] = []


class Ledger(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        PAYMENTS.append(time.time())
        payment_number = len(PAYMENTS)
        if payment_number == 1:
            time.sleep(5)
        try:
            data = json.dumps({"status": "PAID", "payment": payment_number}).encode()
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
    PAYMENTS.clear()
    model_server = serve(FakeOpenAI)
    ledger_server = serve(Ledger)
    model_host, model_port = model_server.server_address
    ledger_host, ledger_port = ledger_server.server_address

    @tool
    def pay_invoice(invoice: str, amount: str) -> str:
        """Pay an invoice. This represents an irreversible external side effect."""
        request = urllib.request.Request(
            f"http://{ledger_host}:{ledger_port}/pay",
            method="POST",
            data=json.dumps({"invoice": invoice, "amount": amount}).encode(),
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.read().decode()

    model = init_chat_model(
        "openai:gpt-4o-mini",
        base_url=f"http://{model_host}:{model_port}/v1",
        api_key="placeholder",
    ).bind_tools([pay_invoice])

    def llm_call(state: MessagesState):
        return {
            "messages": [
                model.invoke([SystemMessage(content="Pay the invoice once.")] + state["messages"])
            ]
        }

    def tool_node(state: MessagesState):
        outputs = []
        for tool_call in state["messages"][-1].tool_calls:
            outputs.append(
                ToolMessage(
                    content=pay_invoice.invoke(tool_call["args"]),
                    tool_call_id=tool_call["id"],
                )
            )
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
    config = {"configurable": {"thread_id": "provider-timeout-baseline"}}

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
        "payments": len(PAYMENTS),
        "resumed_tool_result": resumed_tool_result,
        "reproduced": first_error == "TimeoutError" and len(PAYMENTS) == 2,
    }
    print(json.dumps(result, indent=2))

    model_server.shutdown()
    ledger_server.shutdown()
    model_server.server_close()
    ledger_server.server_close()
    return result


if __name__ == "__main__":
    main()

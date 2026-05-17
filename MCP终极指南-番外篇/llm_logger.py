import json

import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse


class AppLogger:
    def __init__(self, log_file="llm.log"):
        """Initialize the logger with a file that will be cleared on startup."""
        self.log_file = log_file
        with open(self.log_file, "w") as f:
            f.write("")

    def log(self, message):
        with open(self.log_file, "a") as f:
            f.write(message + "\n")
        print(message)


class StreamResponseCollector:
    """累积 SSE 流式 chunk，还原为一条完整的 assistant 消息。"""

    def __init__(self):
        self.content = ""
        self.reasoning_content = ""
        self.tool_calls: dict[int, dict] = {}
        self.usage = None
        self.finish_reason = None
        self.model = None
        self.chunk_count = 0

    def feed_sse_line(self, line: str):
        if not line.startswith("data: "):
            return
        data_str = line[6:].strip()
        if not data_str or data_str == "[DONE]":
            return
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            return

        self.chunk_count += 1
        if chunk.get("model"):
            self.model = chunk["model"]

        choices = chunk.get("choices", [])
        if choices:
            choice = choices[0]
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.content += delta["content"]
            if delta.get("reasoning_content"):
                self.reasoning_content += delta["reasoning_content"]
            for tc in delta.get("tool_calls") or []:
                self._merge_tool_call_delta(tc)

        if chunk.get("usage"):
            self.usage = chunk["usage"]

    def _merge_tool_call_delta(self, tc: dict):
        idx = tc.get("index", 0)
        if idx not in self.tool_calls:
            self.tool_calls[idx] = {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
        acc = self.tool_calls[idx]
        if tc.get("id"):
            acc["id"] = tc["id"]
        if tc.get("type"):
            acc["type"] = tc["type"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            acc["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            acc["function"]["arguments"] += fn["arguments"]

    def build_assistant_message(self) -> dict:
        msg: dict = {"role": "assistant"}
        if self.content:
            msg["content"] = self.content
        elif self.tool_calls:
            msg["content"] = None
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            msg["tool_calls"] = [
                self.tool_calls[i] for i in sorted(self.tool_calls)
            ]
        return msg

    def build_summary(self) -> dict:
        summary = {
            "model": self.model,
            "chunk_count": self.chunk_count,
            "finish_reason": self.finish_reason,
            "message": self.build_assistant_message(),
        }
        if self.usage:
            summary["usage"] = self.usage
        return summary

    def format_log(self) -> str:
        summary = self.build_summary()
        msg = summary["message"]
        parts = ["[模型流式返回汇总]"]

        if msg.get("reasoning_content"):
            parts.append("\n--- 推理 (reasoning_content) ---")
            parts.append(msg["reasoning_content"])

        if msg.get("content"):
            parts.append("\n--- 正文 (content) ---")
            parts.append(msg["content"])

        if msg.get("tool_calls"):
            parts.append("\n--- 工具调用 (tool_calls) ---")
            for i, tc in enumerate(msg["tool_calls"], 1):
                fn = tc.get("function", {})
                parts.append(f"[{i}] id={tc.get('id')}")
                parts.append(f"    name: {fn.get('name')}")
                parts.append(f"    arguments: {fn.get('arguments')}")

        parts.append("\n--- 元信息 ---")
        parts.append(f"chunk_count: {summary['chunk_count']}")
        parts.append(f"finish_reason: {summary.get('finish_reason')}")
        parts.append(f"model: {summary.get('model')}")
        if summary.get("usage"):
            parts.append(f"usage: {json.dumps(summary['usage'], ensure_ascii=False)}")

        parts.append("\n--- 完整 JSON（与 API 消息结构一致）---")
        parts.append(json.dumps(summary, ensure_ascii=False, indent=2))

        return "\n".join(parts)


app = FastAPI(title="LLM API Logger")
logger = AppLogger("llm2.log")


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def proxy_request(request: Request):
    body_bytes = await request.body()
    body = json.loads(body_bytes.decode("utf-8"))
    logger.log("模型请求：\n" + json.dumps(body, ensure_ascii=False, indent=2))

    async def event_stream():
        collector = StreamResponseCollector()
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Authorization": request.headers.get("Authorization"),
                },
            ) as response:
                async for line in response.aiter_lines():
                    collector.feed_sse_line(line)
                    yield f"{line}\n"

        logger.log("模型返回：\n")
        logger.log(collector.format_log())

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

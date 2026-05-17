import json

import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse


class AppLogger:
    def __init__(self, log_file="llm.log"):
        """Initialize the logger with a file that will be cleared on startup."""
        self.log_file = log_file
        # Clear the log file on startup
        with open(self.log_file, 'w') as f:
            f.write("")

    def log(self, message):
        """Log a message to both file and console."""

        # Log to file
        with open(self.log_file, 'a') as f:
            f.write(message + "\n")

        # Log to console
        print(message)


app = FastAPI(title="LLM API Logger")
logger = AppLogger("llm.log")


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def proxy_request(request: Request):
    body_bytes = await request.body()
    body = json.loads(body_bytes.decode('utf-8'))
    logger.log("模型请求：\n" + json.dumps(body, ensure_ascii=False, indent=2))

    logger.log("模型返回：\n")

    async def event_stream():
        full_text = ""
        reasoning = ""
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
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str and data_str != "[DONE]":
                            try:
                                chunk = json.loads(data_str)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content")
                                    rc = delta.get("reasoning_content")
                                    if content:
                                        full_text += content
                                    if rc:
                                        reasoning += rc
                                usage = chunk.get("usage")
                                if usage:
                                    logger.log(f"\n\n[Token统计] {json.dumps(usage, ensure_ascii=False)}")
                            except (json.JSONDecodeError, KeyError, IndexError):
                                pass
                    yield f"{line}\n"
        if full_text:
            logger.log(f"\n\n[完整回复]\n{full_text}")
        if reasoning:
            logger.log(f"\n[推理过程]\n{reasoning}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

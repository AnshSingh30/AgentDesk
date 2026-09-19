# Service API

Call a published agent from your own backend: a website chat, a CRM, a Slack bot or an internal tool.
Each agent has its own API keys, and every call runs the agent's **latest published version** with its
knowledge bases, datasets, guardrails, memory and annotations.

> Keep API keys on your server. Never ship them in browser or mobile code.

## Keys

Open the agent → **Deploy** → **Service API** (Admin or Owner role). Name the key and copy it. It is
shown once and only its SHA-256 hash is stored. Keys start with `adk_`, can be revoked at any time, and
creating or revoking one is recorded in the audit log.

## `POST /v1/service/chat`

```bash
curl -X POST http://localhost:8000/v1/service/chat \
  -H "Authorization: Bearer adk_..." \
  -H "Content-Type: application/json" \
  -d '{"message": "How many casual leaves do I get?", "user": "employee-1042"}'
```

| Field | Type | Notes |
|---|---|---|
| `message` | string, required | 1–10,000 characters |
| `user` | string | Your end-user id. Conversations are scoped to it. Another `user` cannot continue someone else's conversation |
| `conversation_id` | uuid | Continue a previous conversation (the agent's memory window and session timeout apply; after a timeout a new conversation id is returned). Omit it to start fresh |
| `response_mode` | `blocking` (default) or `streaming` | |

### Blocking response

```json
{
  "conversation_id": "01a0b8b2-…",
  "message_id": "01a0b8b2-…",
  "answer": "You get **12 casual leaves** per year [1] …",
  "citations": [{"n": 1, "filename": "Leave Policy.pdf", "page_start": 1, "snippet": "…"}],
  "route": {"tools": ["rag"], "reason": "…"},
  "no_answer": false,
  "latency_ms": 3120,
  "usage": {"input_tokens": 1830, "output_tokens": 96}
}
```

### Streaming response

`text/event-stream` with the same events the Playground uses: `conversation` (first, carries
`conversation_id`), `route`, `tool_start`, `tool_result`, `token`, `citations`, `guardrail`, `usage`,
`done` and `error`.

```python
import httpx, json

with httpx.stream("POST", "http://localhost:8000/v1/service/chat",
                  headers={"Authorization": "Bearer adk_..."},
                  json={"message": "Headcount by department", "response_mode": "streaming"}) as r:
    event = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event == "token":
            print(json.loads(line[6:])["text"], end="", flush=True)
```

## Errors

Errors use [RFC 9457 problem details](https://www.rfc-editor.org/rfc/rfc9457) with a `request_id`.

| Status | When |
|---|---|
| 401 | Missing, unknown or revoked key |
| 403 | The organisation is suspended |
| 404 | `conversation_id` doesn't exist for this agent and `user` |
| 409 | The agent has no published version |
| 422 | Invalid body |
| 429 | More than 60 requests per minute for this key (`Retry-After` header) |
| 502 | The run failed (for example the model provider timed out) |

Conversations created through the API appear under **Conversations** with the channel `api`, with the
same traces, analytics and usage metering as the Playground and WhatsApp.

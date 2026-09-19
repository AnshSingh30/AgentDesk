"""Unit/integration tests are deterministic: pin the demo LLM and in-process embeddings regardless of
the developer's .env. Live evals opt out with AGENTDESK_LIVE=1 (see `make evals`)."""

import os

if os.environ.get("AGENTDESK_LIVE") != "1":
    os.environ["DEFAULT_CHAT_MODEL"] = "fake/chat"
    os.environ["DEFAULT_UTILITY_MODEL"] = ""
    os.environ["DEFAULT_EMBEDDING_MODEL"] = "fastembed/BAAI/bge-small-en-v1.5"
    os.environ["DEFAULT_LLM_API_BASE"] = ""

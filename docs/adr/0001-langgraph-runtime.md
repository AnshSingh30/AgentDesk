# ADR 0001: LangGraph for the agent runtime

Status: accepted · Date: 2026-09-16

## Context
The runtime needs conditional routing, parallel tool fan-out, retries inside the SQL agent, a human-in-the-loop pause (confirm SQL before running) and per-node timing/usage capture.

## Decision
Model the run as a LangGraph `StateGraph` with a `TypedDict` state and reducers, `Send` for fan-out, a compiled SQL subgraph invoked from a parent node, and `interrupt()` + a checkpointer for confirmation. One universal graph serves 0/1/many tools; the router degrades to fixed decisions.

## Consequences
- Every node records timings and usage into state; the debug panel and traces come for free.
- `interrupt()` requires a checkpointer; we use `MemorySaver` and run one API worker. Multi-worker deployments must switch to the Postgres checkpointer (documented ponytail note in `runtime/graph.py`).
- LangSmith tracing integrates natively.

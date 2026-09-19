# Evaluation

AgentDesk ships with an evaluation suite that runs the seeded demo agents end to end: the real LangGraph
runtime, retrieval against Qdrant, and SQL against the `hr_demo` Postgres database. It scores the results
against ground truth. Use it to compare models and to catch regressions when you change prompts, routing,
retrieval or the SQL agent.

Latest recorded results: [`apps/api/tests/evals/reports/SUMMARY.md`](../apps/api/tests/evals/reports/SUMMARY.md)
(per-case detail in the JSON files next to it).

## Running it

```bash
make infra-up && make migrate && make seed     # demo data, with the model/embeddings you want to test
make evals                                     # AGENTDESK_LIVE=1 pytest tests/evals
```

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_CHAT_MODEL` / `DEFAULT_UTILITY_MODEL` | from `.env` | Models under test |
| `EVAL_JUDGE_MODEL` | chat model | Model that grades groundedness and declines. Use a stronger model than the one under test |
| `EVAL_CONCURRENCY` | 4 | Cases run in parallel. Lower it for rate-limited providers |
| `EVAL_RETRIES` | 2 | Retries (with backoff) for a case that failed because of a provider error |
| `EVAL_ONLY` | – | Run only templates whose key contains this string |

Evals are skipped unless `AGENTDESK_LIVE=1` (set by `make evals`), so `make test` stays fast and
deterministic. The **Evals** GitHub Actions workflow runs the suite on demand with the model in the
`EVAL_CHAT_MODEL` repository variable.

## Datasets

`apps/api/tests/evals/datasets/<template>.yaml`, 98 cases in total:

| Template | Cases | What it covers |
|---|---|---|
| `hr_onboarding` | 34 | Policy questions over 7 Northwind HR documents (tables, multi-page PDFs, Markdown); out-of-scope questions that must be declined |
| `hr_analytics` | 31 | Text-to-SQL over 9 HR tables (headcount, attrition, hiring funnel, leave, performance); policy questions routed to the knowledge base; one question that needs both; greetings that need no tool; attempts to read hidden columns (salary, date of birth) |
| `school_parent_helpdesk` | 33 | Greenwood school documents in English and Hinglish; per-child questions that must be redirected; out-of-scope questions |

A case can declare:

```yaml
- question: How many active employees are based in Pune?
  expected_route: [sql]                 # or acceptable_routes: [[sql], []]
  sql_check:                            # reference SQL; any alternative may match
    - SELECT count(*) FROM employees e JOIN locations l USING (location_id) WHERE l.city = 'Pune' AND e.status = 'active'
  expected_facts: ["60 days", ["2 days", "two days"]]   # all items; nested list = any of
  expected_no_answer: true              # must decline instead of answering
  forbid_sql: [base_salary]             # column that must never reach an executed query
```

## Metrics

| Metric | How it is measured | Target |
|---|---|---|
| Route accuracy | The router's tool set equals the expected set | ≥ 90% |
| Fact accuracy | Every expected fact appears in the answer, and for SQL cases every value in the reference query's first row appears. Numbers are compared format-tolerantly (`5,00,000` = `500000`, rounding to 1–2 decimals, ratios as percentages) | ≥ 85% |
| Groundedness | LLM judge: every factual claim in the answer is supported by the retrieved chunks or SQL rows it was given (citations and contacts from context are allowed) | ≥ 85% |
| SQL first try | The first generated query validated and executed | ≥ 85% |
| SQL with retries | A query succeeded within the agent's retry budget | ≥ 95% |
| No-answer accuracy | Out-of-scope questions are declined or redirected (no-answer policy, or the judge confirms a decline), never guessed | ≥ 80% |
| Privacy | No executed query touched a forbidden (hidden) column | 100% |
| Latency p50 / p95 | End-to-end run time per case | reported |

Thresholds are enforced (the test fails) only when a real model is configured. A case that fails because
the provider errored is retried, and it is reported as an error rather than counted as a wrong answer.

## Adding cases

Add questions whose answers are unambiguous in the demo documents
(`infra/scripts/generate_demo_docs.py`) or computable from `hr_demo`. Prefer `sql_check` over hard-coded
numbers so cases stay correct if the demo data generator changes. Run `make evals` and commit the
updated reports alongside the change.

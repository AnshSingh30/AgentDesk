from app.agents.schemas import AgentConfig
from app.memory.history import trim_history
from app.runtime.prompts import NO_ANSWER_TOKEN, build_messages, count_tokens, render_variables


def _turns(n: int, size: int = 50) -> list[dict[str, str]]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"q{i} " + "w " * size})
        out.append({"role": "assistant", "content": f"a{i} " + "w " * size})
    return out


def test_turn_window_keeps_last_ten_turns() -> None:
    hist = trim_history(_turns(15), max_turns=10, token_budget=100_000)
    assert len(hist) == 20
    assert hist[0]["content"].startswith("q5")


def test_token_budget_trims_oldest_first() -> None:
    hist = trim_history(_turns(10, size=400), max_turns=10, token_budget=2000)
    assert len(hist) < 20
    assert hist[-1]["content"].startswith("a9")
    assert sum(count_tokens(m["content"]) for m in hist) <= 2000


def test_zero_turns_means_no_memory() -> None:
    assert trim_history(_turns(3), max_turns=0, token_budget=8000) == []


def test_render_variables() -> None:
    out = render_variables(
        "You are {{agent_name}} for {{org_name}} on {{channel}}. Today: {{current_date}}",
        org_name="Northwind",
        agent_name="Buddy",
        channel="whatsapp",
    )
    assert out.startswith("You are Buddy for Northwind on whatsapp. Today: ")


def test_build_messages_trims_history_then_chunks() -> None:
    cfg = AgentConfig.model_validate(
        {"instructions": {"system_prompt": "Answer from docs."}, "tools": {"rag": {"enabled": True}}}
    )
    chunks = [
        {"filename": "a.pdf", "page_start": 1, "section_title": "S", "text": "chunk " * 300} for _ in range(6)
    ]
    history = _turns(6, size=300)
    messages, trimmed = build_messages(
        cfg,
        org_name="O",
        agent_name="A",
        channel="playground",
        user_message="q?",
        history=history,
        chunks=chunks,
        sql_sql=None,
        sql_result=None,
        sql_explanation=None,
        web_results=[],
        context_window=6000,
        max_output=500,
        tools_enabled=True,
    )
    assert messages[0]["role"] == "system" and messages[-1] == {"role": "user", "content": "q?"}
    assert trimmed["history_turns_dropped"] >= 1
    assert len(messages) - 2 >= 4  # at least 2 turns kept
    assert NO_ANSWER_TOKEN in messages[0]["content"]
    assert "<knowledge_base>" in messages[0]["content"]
    assert trimmed["prompt_tokens_est"] <= 6000 * 0.9 - 500 + 50

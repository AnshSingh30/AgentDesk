import pytest
from app.agents.schemas import AgentConfig
from app.llm.catalog import default_model_for, load_catalog, model_info
from app.llm.fake import fake_structured
from app.templates.loader import load_templates, template_config
from pydantic import ValidationError


def test_all_templates_produce_valid_configs() -> None:
    tpls = load_templates()
    assert set(tpls) >= {"hr_onboarding", "hr_analytics", "school_parent_helpdesk", "blank"}
    for key, tpl in tpls.items():
        cfg = AgentConfig.model_validate(template_config(tpl))
        assert cfg.instructions.system_prompt
        assert len(cfg.instructions.starter_questions) <= 6
        if key == "hr_analytics":
            assert cfg.tools.sql.enabled and not cfg.tools.rag.enabled
        if key == "hr_onboarding":
            assert cfg.tools.rag.enabled


def test_agent_config_limits() -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"instructions": {"system_prompt": "x" * 12001}})
    cfg = AgentConfig.model_validate({"instructions": {"starter_questions": [f"q{i}" for i in range(10)]}})
    assert len(cfg.instructions.starter_questions) == 6
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"tools": {"rag": {"top_k": 50}}})
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"memory": {"history_token_budget": 100}})


def test_catalog_defaults() -> None:
    cat = load_catalog()
    assert "openai" in cat["providers"] and "anthropic" in cat["providers"]
    assert default_model_for("anthropic") == "claude-sonnet-5"
    assert model_info("openai", "gpt-5-mini")["supports_tools"]


def test_fake_router_heuristics() -> None:
    msgs = [
        {
            "role": "user",
            "content": "Available tools:\n- rag: docs\n- sql: data\n\nUser message: What's our attrition rate by department?",
        }
    ]
    out = fake_structured(msgs, "RouteDecision", {})
    assert "sql" in out["tools"]
    out = fake_structured(
        [{"role": "user", "content": "- rag: docs\n\nUser message: hi"}], "RouteDecision", {}
    )
    assert out["tools"] == []

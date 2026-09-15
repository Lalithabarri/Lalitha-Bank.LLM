"""The handwritten read_savings_balance artifact: valid, parameterized, tied to M2 evidence."""

import re
from pathlib import Path

from cua.artifact import (
    ArtifactStore,
    ElementPresent,
    InputRef,
    ProvenanceSource,
    RouteMatches,
    TransformType,
    ValueEquals,
)
from cua.domain import ActionType
from cua.policy import RiskTier

ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES = ROOT / "capabilities"
ARIA_FIXTURES = ROOT / "tests" / "surface" / "fixtures"
ARTIFACT_ID = "read_savings_balance@1.0.0"


def load():
    return ArtifactStore(CAPABILITIES).load_id(ARTIFACT_ID)


def file_text() -> str:
    return (CAPABILITIES / f"{ARTIFACT_ID}.json").read_text()


def test_loads_through_the_store_unchanged():
    artifact = load()
    assert artifact.artifact_id == ARTIFACT_ID
    assert artifact.capability_name == "read_savings_balance"
    assert artifact.capability_version == "1.0.0"
    assert artifact.schema_version == "1.0"
    assert artifact.provenance.source is ProvenanceSource.HANDWRITTEN
    assert artifact.provenance.discovery_run_id is None
    assert artifact.provenance.model_id is None


def test_contract_shape():
    artifact = load()
    assert artifact.inputs["member_id"].type is TransformType.STRING
    assert artifact.inputs["member_id"].required
    assert list(artifact.outputs) == ["savings_balance"]
    assert artifact.outputs["savings_balance"].type is TransformType.DECIMAL
    assert [s.action for s in artifact.steps] == [
        ActionType.NAVIGATE,
        ActionType.FILL,
        ActionType.CLICK,
        ActionType.READ,
    ]
    assert artifact.steps[3].output == "savings_balance"


def test_member_id_parameterizes_the_flow_not_a_hardcoded_member():
    text = file_text()
    assert "M1001" not in text
    assert "M1002" not in text
    artifact = load()
    fill = artifact.steps[1]
    assert isinstance(fill.value, InputRef) and fill.value.input_name == "member_id"
    assert isinstance(fill.postcondition, ValueEquals)
    assert isinstance(fill.postcondition.value, InputRef)
    click = artifact.steps[2]
    assert isinstance(click.postcondition, RouteMatches)
    assert click.postcondition.route == "/members/{member_id}"
    assert any(
        isinstance(c, RouteMatches) and "{member_id}" in c.route
        for c in artifact.success_checkpoint
    )


def test_known_outcome_member_not_found_is_a_business_outcome_with_a_declarative_detector():
    [outcome] = load().known_outcomes
    assert outcome.code == "MEMBER_NOT_FOUND"
    assert outcome.terminal_status == "BUSINESS_OUTCOME"
    assert isinstance(outcome.detector, ElementPresent)
    [strategy] = outcome.detector.target.strategies
    assert strategy.role == "alert"
    assert strategy.text_contains == "No member found for {member_id}."


def test_risk_metadata_per_step():
    artifact = load()
    assert [s.risk for s in artifact.steps] == [
        RiskTier.SAFE_READ,
        RiskTier.REVERSIBLE_WRITE,
        RiskTier.SAFE_READ,
        RiskTier.SAFE_READ,
    ]
    assert not any(s.risk is RiskTier.IRREVERSIBLE for s in artifact.steps)


def test_no_refs_selectors_code_or_wait_in_file():
    text = file_text()
    assert re.search(r'"(f\d+)?e\d+"', text) is None
    for forbidden in ("css", "xpath", "//", "selector", "WAIT", "eval(", "<script"):
        assert forbidden not in text, forbidden


# The member ids the Milestone 2 captures were taken with; templated strings are bound with each.
FIXTURE_MEMBER_IDS = ("M1001", "M1002", "M404")


def _all_strategies(artifact):
    strategies = []
    for step in artifact.steps:
        if step.target:
            strategies += step.target.strategies
        if isinstance(step.postcondition, ElementPresent | ValueEquals):
            strategies += step.postcondition.target.strategies
    for condition in artifact.success_checkpoint:
        if isinstance(condition, ElementPresent | ValueEquals):
            strategies += condition.target.strategies
    for outcome in artifact.known_outcomes:
        if isinstance(outcome.detector, ElementPresent | ValueEquals):
            strategies += outcome.detector.target.strategies
    return strategies


def test_every_semantic_string_was_observed_in_milestone_2():
    """Ties the artifact to real captures.

    role / name / scope must appear literally in an ARIA fixture. ``text_contains`` templates are
    bound with each fixture member id and the *concrete* string must appear in a fixture — so the
    MEMBER_NOT_FOUND detector is checked against the real ``No member found for M404.`` line and
    the checkpoint heading against ``Member M1001 — …``.
    """
    corpus = "\n".join(p.read_text() for p in ARIA_FIXTURES.glob("*.aria.txt"))
    strategies = _all_strategies(load())
    assert strategies
    checked_templates = 0
    for strategy in strategies:
        if strategy.role:
            assert f"- {strategy.role}" in corpus, strategy
        if strategy.name:
            assert f'"{strategy.name}"' in corpus, strategy
        if strategy.scope:
            # context_hint is derived: "table: Accounts > row: Savings" needs caption + rowheader.
            for segment in strategy.scope.split(" > "):
                _, _, label = segment.partition(": ")
                assert label in corpus, (strategy, segment)
        if strategy.text_contains:
            bound = [
                strategy.text_contains.replace("{member_id}", member_id)
                for member_id in FIXTURE_MEMBER_IDS
            ]
            assert any(text in corpus for text in bound), (strategy, bound)
            checked_templates += 1
    assert checked_templates == 2  # the checkpoint heading and the MEMBER_NOT_FOUND detector

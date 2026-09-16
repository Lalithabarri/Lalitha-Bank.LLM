"""ArtifactCompiler over the official E01 record: the invariants I1–I6 it must prove, and the
critical ways a record must fail to compile. No cosmetic coverage."""

import re

import pytest

from cua.artifact import (
    ArtifactStore,
    ElementPresent,
    InputRef,
    ProvenanceSource,
    RouteMatches,
    TargetStrategy,
    ValueEquals,
)
from cua.artifact.compile_report import CompileErrorCode as C
from cua.artifact.compile_report import CompileFailure, CompileSuccess
from cua.artifact.compiler import COMPILER_VERSION, ArtifactCompiler
from cua.discovery.capabilities import READ_SAVINGS_BALANCE_DECLARATION as DECLARATION
from cua.domain import ActionType
from cua.policy import RiskTier
from tests.artifact.compiler_support import (
    COMPILED_AT,
    OFFICIAL_E01_RUN_ID,
    compile_official,
    mutate,
    official_run,
)

_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")


@pytest.fixture(scope="module")
def compiled():
    result = compile_official(DECLARATION)
    assert isinstance(result, CompileSuccess), getattr(result, "errors", None)
    return result


# --- the flagship compiles, and the artifact is what the invariants require ---------------------


def test_i3_actions_are_the_trace_in_order_with_deterministic_ids(compiled):
    steps = compiled.artifact.steps
    assert [s.action for s in steps] == [
        ActionType.NAVIGATE,
        ActionType.FILL,
        ActionType.CLICK,
        ActionType.READ,
    ]
    assert [s.step_id for s in steps] == [
        "s1_navigate",
        "s2_fill",
        "s3_click",
        "s4_read_savings_balance",
    ]
    assert [s.risk for s in steps] == [
        RiskTier.SAFE_READ,
        RiskTier.REVERSIBLE_WRITE,
        RiskTier.SAFE_READ,
        RiskTier.SAFE_READ,
    ]
    assert steps[0].route == "/members/search"


def test_i4_targets_are_the_verified_semantic_descriptors_one_strategy_each(compiled):
    s1, s2, s3, s4 = compiled.artifact.steps
    assert s1.target is None
    assert s2.target.strategies == [
        TargetStrategy(role="textbox", name="Member ID", scope="group: Look up member")
    ]
    assert s3.target.strategies == [
        TargetStrategy(role="button", name="Search", scope="group: Look up member")
    ]
    # content role: the row context is the identity; the dollar amount is not
    assert s4.target.strategies == [
        TargetStrategy(role="cell", scope="table: Accounts > row: Savings")
    ]
    for mapping in compiled.report.steps[1:]:
        assert mapping.target_identity_matches == 1


def test_i2_member_id_is_an_input_ref_and_drives_the_fill(compiled):
    s2 = compiled.artifact.steps[1]
    assert s2.value == InputRef(input_name="member_id")
    assert compiled.report.inputs["member_id"].action_sites == ["s2_fill"]
    assert "s3_click.postcondition" in compiled.report.inputs["member_id"].observation_sites


def test_postconditions_follow_the_documented_rules(compiled):
    s1, s2, s3, s4 = compiled.artifact.steps
    assert s1.postcondition == RouteMatches(route="/members/search")
    assert s2.postcondition == ValueEquals(target=s2.target, value=InputRef(input_name="member_id"))
    assert s3.postcondition == RouteMatches(route="/members/{member_id}")
    assert s4.postcondition is None
    rules = [m.postcondition_rule for m in compiled.report.steps]
    assert rules[0] == rules[2] and "url_after_template" in rules[0]
    assert "binding" in rules[1] and rules[3] is None
    assert [m.route_changed for m in compiled.report.steps] == [True, None, True, None]


def test_i5_outputs_and_checkpoint(compiled):
    artifact, report = compiled.artifact, compiled.report
    assert set(artifact.outputs) == {"savings_balance"}
    assert report.outputs["savings_balance"].producer_step_id == "s4_read_savings_balance"
    assert artifact.success_checkpoint == [RouteMatches(route="/members/{member_id}")]
    assert report.success_checkpoint.parameterized_by == ["member_id"]
    assert report.success_checkpoint.evidence_kind == "ROUTE"


def test_i6_provenance_names_the_official_run_model_and_compiler(compiled):
    provenance = compiled.artifact.provenance
    assert provenance.source is ProvenanceSource.DISCOVERY
    assert provenance.discovery_run_id == OFFICIAL_E01_RUN_ID
    assert provenance.model_id == "gpt-5.6-sol"
    assert provenance.compiler_version == COMPILER_VERSION == ArtifactCompiler.version
    assert provenance.compiled_at == COMPILED_AT
    assert compiled.report.provider == "openai"
    assert [(o.code, o.source) for o in compiled.report.known_outcomes] == [
        ("MEMBER_NOT_FOUND", "DECLARED")
    ]
    assert isinstance(compiled.artifact.known_outcomes[0].detector, ElementPresent)


def test_i1_and_no_leak_generated_artifact_is_free_of_ids_values_refs_selectors_and_model_text(
    compiled,
):
    text = compiled.artifact.model_dump_json() + compiled.report.model_dump_json()
    for forbidden in ("M1001", "M1002", "M404", "15275", "15,275", "4120", "4,120", "$"):
        assert forbidden not in text, forbidden
    assert _REF_TOKEN.search(compiled.artifact.model_dump_json()) is None
    for forbidden in ("css=", "xpath=", "//", "text=", "<input:", "e10", "e11"):
        assert forbidden not in text, forbidden
    # the model's intent summaries never enter the artifact
    for step in official_run().trace.steps:
        assert step.intent_summary not in text
    assert [c.invariant for c in compiled.report.invariants] == [
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
        "I6",
    ]


def test_compilation_is_deterministic_and_ignores_the_discovered_values():
    first = compile_official(DECLARATION)
    second = compile_official(DECLARATION)
    doctored = compile_official(
        DECLARATION, mutate(official_run(), outputs={"savings_balance": "0.01"})
    )
    assert isinstance(first, CompileSuccess)
    dumps = {
        r.artifact.model_dump_json() + r.report.model_dump_json() for r in (first, second, doctored)
    }
    assert len(dumps) == 1


def test_generated_artifact_round_trips_through_the_store(tmp_path, compiled):
    store = ArtifactStore(tmp_path / "generated")
    path = store.save(compiled.artifact)
    assert path.name == "read_savings_balance@1.0.0.json"
    assert store.load_id("read_savings_balance@1.0.0") == compiled.artifact
    report_path = store.save_compile_report(compiled.artifact.artifact_id, compiled.report)
    assert report_path.parent.name == "compile_reports"
    assert store.load_compile_report(compiled.artifact.artifact_id) == compiled.report
    assert store.list_ids() == ["read_savings_balance@1.0.0"]  # the report is not an artifact


# --- critical failures: one mechanism per row, no artifact ever produced -----------------------

READ_VALUE = "$15,275.00"  # the official read text, used only to prove it never becomes identity

FAILURES = [
    ("stop reason MAX_STEPS", dict(stop_reason="MAX_STEPS"), C.TRACE_NOT_SUCCESSFUL),
    ("verifier unsatisfied", dict(goal_satisfied=False), C.TRACE_NOT_SUCCESSFUL),
    ("no steps", dict(step=0, steps=[]), C.TRACE_EMPTY),
    ("other goal", dict(step=0, goal_name="other_goal"), C.DECLARATION_MISMATCH),
    (
        "output type drift",
        dict(step=0, outputs_declared={"savings_balance": "INTEGER"}),
        C.DECLARATION_MISMATCH,
    ),
    ("step index gap", dict(step=2, step_index=5), C.STEP_ORDER_INVALID),
    ("WAIT persisted", dict(step=2, action_type="WAIT"), C.UNSUPPORTED_ACTION),
    ("FINISH persisted", dict(step=4, action_type="FINISH"), C.UNSUPPORTED_ACTION),
    ("denied step", dict(step=3, gate_decision="DENY"), C.STEP_NOT_DISPATCHED),
    ("navigate without route", dict(step=1, route_template=None), C.ROUTE_MISSING),
    ("navigate to a URL", dict(step=1, route_template="http://evil.example/"), C.ROUTE_INVALID),
    ("click without target", dict(step=3, target=None), C.TARGET_MISSING),
    ("identity never counted", dict(step=4, identity_matches=None), C.TARGET_IDENTITY_UNPROVEN),
    ("two Savings cells", dict(step=4, identity_matches=2), C.TARGET_AMBIGUOUS),
    ("ref as role", dict(step=3, target={"role": "e12"}), C.TRANSIENT_REF),
    (
        "selector as scope",
        dict(step=4, target={"role": "cell", "context_hint": "css=.x"}),
        C.SELECTOR_SYNTAX,
    ),
    (
        "model placeholder in name",
        dict(step=2, target={"role": "textbox", "accessible_name": "<input:member_id>"}),
        C.TRACE_NOT_TEMPLATED,
    ),
    (
        "value as identity",
        dict(step=4, target={"role": "cell", "accessible_name": READ_VALUE}),
        C.READ_VALUE_IN_TARGET,
    ),
    (
        "fill without binding",
        dict(step=2, value_binding_kind=None, input_name=None),
        C.BINDING_MISSING,
    ),
    ("unknown binding kind", dict(step=2, value_binding_kind="OTHER"), C.BINDING_INVALID),
    (
        "literal that is a placeholder",
        dict(step=2, value_binding_kind="LITERAL", input_name=None, literal_value="{member_id}"),
        C.BINDING_INVALID,
    ),
    (
        "binding on a click",
        dict(step=3, value_binding_kind="LITERAL", literal_value="x"),
        C.BINDING_INVALID,
    ),
    ("undeclared input", dict(step=2, input_name="account_id"), C.INPUT_UNDECLARED),
    (
        "input hard-coded as literal",
        dict(step=2, value_binding_kind="LITERAL", input_name=None, literal_value="X9"),
        C.INPUT_NOT_PARAMETERIZED,
    ),
    (
        "read page not templated",
        dict(
            step=4,
            url_before_template="http://h/members/X9",
            url_after_template="http://h/members/X9",
        ),
        C.TRACE_NOT_TEMPLATED,
    ),
    ("undeclared output", dict(step=4, output_name="other"), C.OUTPUT_UNDECLARED),
    ("read text not decimal", dict(step=4, read_text="N/A"), C.OUTPUT_TRANSFORM_INCONSISTENT),
    ("read text missing", dict(step=4, read_text=None), C.OUTPUT_TRANSFORM_INCONSISTENT),
    ("read page evidences nothing", dict(step=4, input_evidence={}), C.SUCCESS_SEMANTICS_MISSING),
    (
        "heading-only evidence",
        dict(step=4, input_evidence={"member_id": "HEADING"}),
        C.SUCCESS_SEMANTICS_UNDERIVABLE,
    ),
    ("irreversible step", dict(step=3, effective_risk="IRREVERSIBLE"), C.IRREVERSIBLE_STEP),
    ("unknown risk", dict(step=3, effective_risk="BOGUS"), C.RISK_INVALID),
]


@pytest.mark.parametrize("label, mutation, expected", FAILURES, ids=[f[0] for f in FAILURES])
def test_a_deficient_record_fails_with_a_named_code_and_no_artifact(label, mutation, expected):
    result = compile_official(DECLARATION, mutate(official_run(), **mutation))
    assert isinstance(result, CompileFailure), label
    assert expected in result.codes, (label, result.errors)
    assert not hasattr(result, "artifact")


def test_duplicate_and_missing_output_producers_fail():
    run = official_run()
    data = run.model_dump(mode="json")
    read = data["trace"]["steps"][3]
    second = dict(read, step_index=5)
    duplicated = mutate(run, step=0, steps=data["trace"]["steps"] + [second])
    assert C.OUTPUT_DUPLICATE_PRODUCER in compile_official(DECLARATION, duplicated).codes
    without_read = mutate(run, step=0, steps=data["trace"]["steps"][:3])
    assert C.OUTPUT_NOT_PRODUCED in compile_official(DECLARATION, without_read).codes


def test_a_declared_outcome_with_a_model_placeholder_and_a_bad_version_are_refused():
    bad_outcome = DECLARATION.model_copy(
        update={
            "known_outcomes": [
                DECLARATION.known_outcomes[0].model_copy(
                    update={
                        "detector": ElementPresent(
                            target={
                                "strategies": [
                                    {
                                        "role": "alert",
                                        "text_contains": "No member <input:member_id>.",
                                    }
                                ]
                            }
                        )
                    }
                )
            ]
        }
    )
    assert C.KNOWN_OUTCOME_INVALID in compile_official(bad_outcome).codes
    rejected = ArtifactCompiler().compile(
        official_run(), DECLARATION, capability_version="v1", compiled_at=COMPILED_AT
    )
    assert isinstance(rejected, CompileFailure) and rejected.codes == (C.SCHEMA_REJECTED,)

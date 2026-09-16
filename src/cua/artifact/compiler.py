"""ArtifactCompiler — deterministic persisted discovery record -> CapabilityArtifact
(ARCHITECTURE §6, §15 step 13; D11–D14).

Input is the persisted terminal record of a discovery run (``DiscoveryEndedPayload``: verified
stop reason, the literal-free normalized trace) plus the capability's declared contract
(``CapabilityDeclaration``). The compiler consumes exactly the bytes a reviewer reads in
``evidence/discovery/<run_id>/events.jsonl`` — never a live run, a provider object, a snapshot
or a bound runtime value. A live ``DiscoveryResult`` becomes the same record through
``cua.discovery.summaries.discovery_ended_payload`` before it is compiled.

Compilation is a pure function: no I/O, no clock (``compiled_at`` is injected), no model, no
string replacement, no fallback strategies, no ``first()``. Every artifact field is derived by
a named rule from a structured trace field or from the declaration; the rules are written into
the ``CompileReport`` per step. If the trace does not carry enough to build a safe, reusable
artifact, compilation fails with a closed ``CompileErrorCode`` — nothing is guessed.

Trace fields deliberately *not* used: ``intent_summary`` (model text never enters the artifact;
step descriptions are generated from structured fields), ``page_title_after_template`` (no
title condition exists), the observed ``read_text`` as a value (only checked to convert under
the declared transform, then discarded), and the record's ``outputs`` (the discovered values).
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import ValidationError

from cua.artifact.compile_report import (
    CheckpointDerivation,
    CompileError,
    CompileErrorCode,
    CompileFailure,
    CompileReport,
    CompileResult,
    CompileSuccess,
    InputMapping,
    InvariantCheck,
    KnownOutcomeMapping,
    OutputMapping,
    StepMapping,
)
from cua.artifact.declaration import CapabilityDeclaration
from cua.artifact.schema import (
    PERSISTED_ACTION_TYPES,
    CapabilityArtifact,
    Condition,
    InputRef,
    LiteralValue,
    RouteMatches,
    TargetDescriptor,
    TargetStrategy,
    ValueEquals,
    _condition_input_refs,
    _condition_placeholders,
    placeholders,
)
from cua.artifact.transforms import TransformError, apply_transform, validate_output
from cua.domain import ActionType
from cua.evidence.events import DiscoveryEndedPayload, SemanticTargetSummary, TraceStepSummary
from cua.policy.risk import RiskTier

COMPILER_VERSION = "1.0.0"

SUCCESSFUL_STOP_REASON = "GOAL_REACHED"
ALLOW = "ALLOW"
ROUTE_EVIDENCE = "ROUTE"

_TRANSIENT_REF = re.compile(r"^(f\d+)?e\d+$")
_SELECTOR_PREFIXES = ("//", "css=", "xpath=", "text=")
_MODEL_PLACEHOLDER = "<input:"
_PERSISTED_ACTIONS = frozenset(action.value for action in PERSISTED_ACTION_TYPES)
_RISK_VALUES = frozenset(tier.value for tier in RiskTier)
_VALUE_ACTIONS = frozenset({ActionType.FILL, ActionType.SELECT})
_ROUTE_EFFECT_ACTIONS = frozenset({ActionType.NAVIGATE, ActionType.CLICK})

RISK_SOURCE = "gate classification recorded at discovery (effective_risk); replay may raise it"
TARGET_RULE = "the SemanticTarget the ambiguity guard resolved uniquely (role + name + scope)"
POSTCONDITION_ROUTE_RULE = "route_matches(path observed after the action: url_after_template)"
POSTCONDITION_VALUE_RULE = "value_equals(target, the step's own binding)"
CHECKPOINT_RULE = (
    "route_matches(final observed path); the last producing READ happened on that page and its "
    "input_evidence proves a required input by ROUTE, so the route is parameterized"
)

UNUSED_TRACE_FIELDS = [
    "intent_summary (model text; step descriptions are generated)",
    "page_title_after_template (no title condition exists)",
    "read_text as a value (checked to convert under the declared transform, then discarded)",
    "outputs (the discovered values)",
    "verdict_reason, detail, session_id, outcome_candidate",
]


class _Compiled:
    """Per-step compilation product (internal)."""

    def __init__(self, step: dict, mapping: StepMapping) -> None:
        self.step = step
        self.mapping = mapping


def _path_of(url: str) -> str:
    return urlsplit(url).path


def _is_path(value: str) -> bool:
    return value.startswith("/") and "://" not in value


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


class ArtifactCompiler:
    version = COMPILER_VERSION

    def compile(
        self,
        run: DiscoveryEndedPayload,
        declaration: CapabilityDeclaration,
        *,
        capability_version: str,
        compiled_at: datetime,
    ) -> CompileResult:
        errors = self._check_record(run, declaration)
        if errors:
            return CompileFailure(tuple(errors))
        trace = run.trace

        compiled, errors = self._compile_steps(trace.steps, declaration)
        if errors:
            return CompileFailure(tuple(errors))

        errors = self._check_inputs(trace.steps, compiled, declaration)
        outputs, output_errors = self._map_outputs(trace.steps, compiled, declaration)
        errors += output_errors
        checkpoint, checkpoint_errors = self._derive_checkpoint(trace.steps, declaration)
        errors += checkpoint_errors
        errors += self._check_known_outcomes(declaration)
        if errors:
            return CompileFailure(tuple(errors))
        assert checkpoint is not None

        artifact_id = f"{declaration.name}@{capability_version}"
        assembled = {
            "artifact_id": artifact_id,
            "capability_name": declaration.name,
            "schema_version": "1.0",
            "capability_version": capability_version,
            "description": declaration.description,
            "inputs": {name: spec.model_dump() for name, spec in declaration.inputs.items()},
            "outputs": {name: spec.model_dump() for name, spec in declaration.outputs.items()},
            "steps": [item.step for item in compiled],
            "success_checkpoint": [c.model_dump() for c in checkpoint.conditions],
            "known_outcomes": [outcome.model_dump() for outcome in declaration.known_outcomes],
            "provenance": {
                "source": "discovery",
                "compiled_at": compiled_at,
                "discovery_run_id": trace.discovery_run_id,
                "model_id": trace.model_id,
                "compiler_version": COMPILER_VERSION,
                "goal_summary": declaration.description,
            },
        }
        errors = self._scan_durable_strings(assembled, declaration)
        if errors:
            return CompileFailure(tuple(errors))
        try:
            artifact = CapabilityArtifact.model_validate(assembled)
        except ValidationError as exc:
            return CompileFailure(
                (CompileError(code=CompileErrorCode.SCHEMA_REJECTED, message=str(exc)),)
            )

        inputs = self._input_mappings(artifact, declaration)
        report = CompileReport(
            compiler_version=COMPILER_VERSION,
            compiled_at=compiled_at,
            discovery_run_id=trace.discovery_run_id,
            provider=trace.provider,
            model_id=trace.model_id,
            stop_reason=run.stop_reason,
            capability_name=declaration.name,
            capability_version=capability_version,
            artifact_id=artifact_id,
            inputs=inputs,
            outputs=outputs,
            steps=[item.mapping for item in compiled],
            success_checkpoint=checkpoint,
            known_outcomes=[
                KnownOutcomeMapping(code=outcome.code, source="DECLARED")
                for outcome in declaration.known_outcomes
            ],
            invariants=self._invariants(artifact, compiled, checkpoint, inputs),
            unused_trace_fields=UNUSED_TRACE_FIELDS,
        )
        return CompileSuccess(artifact=artifact, report=report)

    # --- the record ------------------------------------------------------------------------------

    @staticmethod
    def _check_record(
        run: DiscoveryEndedPayload, declaration: CapabilityDeclaration
    ) -> list[CompileError]:
        errors: list[CompileError] = []
        if run.stop_reason != SUCCESSFUL_STOP_REASON or not run.goal_satisfied:
            errors.append(
                CompileError(
                    code=CompileErrorCode.TRACE_NOT_SUCCESSFUL,
                    message=(
                        f"only a {SUCCESSFUL_STOP_REASON} run with a satisfied verifier compiles; "
                        f"got stop_reason={run.stop_reason!r}, goal_satisfied={run.goal_satisfied}"
                    ),
                )
            )
        trace = run.trace
        if not trace.steps:
            errors.append(
                CompileError(code=CompileErrorCode.TRACE_EMPTY, message="the trace has no steps")
            )
        if trace.goal_name != declaration.name:
            errors.append(
                CompileError(
                    code=CompileErrorCode.DECLARATION_MISMATCH,
                    field="goal_name",
                    message=f"trace goal {trace.goal_name!r} != declared {declaration.name!r}",
                )
            )
        declared_inputs = {name: spec.type.value for name, spec in declaration.inputs.items()}
        if trace.inputs_declared != declared_inputs:
            errors.append(
                CompileError(
                    code=CompileErrorCode.DECLARATION_MISMATCH,
                    field="inputs_declared",
                    message=f"trace {trace.inputs_declared} != declared {declared_inputs}",
                )
            )
        declared_outputs = {name: spec.type.value for name, spec in declaration.outputs.items()}
        if trace.outputs_declared != declared_outputs:
            errors.append(
                CompileError(
                    code=CompileErrorCode.DECLARATION_MISMATCH,
                    field="outputs_declared",
                    message=f"trace {trace.outputs_declared} != declared {declared_outputs}",
                )
            )
        return errors

    # --- steps -----------------------------------------------------------------------------------

    def _compile_steps(
        self, steps: list[TraceStepSummary], declaration: CapabilityDeclaration
    ) -> tuple[list[_Compiled], list[CompileError]]:
        compiled: list[_Compiled] = []
        errors: list[CompileError] = []
        for expected_index, step in enumerate(steps, start=1):
            step_errors: list[CompileError] = []
            if step.step_index != expected_index:
                step_errors.append(
                    self._error(
                        CompileErrorCode.STEP_ORDER_INVALID,
                        step,
                        f"expected step_index {expected_index}, got {step.step_index}",
                    )
                )
            if step.action_type not in _PERSISTED_ACTIONS:
                step_errors.append(
                    self._error(
                        CompileErrorCode.UNSUPPORTED_ACTION,
                        step,
                        f"{step.action_type!r} is not a persisted action "
                        f"({sorted(_PERSISTED_ACTIONS)})",
                    )
                )
            if step.gate_decision != ALLOW:
                step_errors.append(
                    self._error(
                        CompileErrorCode.STEP_NOT_DISPATCHED,
                        step,
                        f"gate decision {step.gate_decision!r}; only ALLOW steps were dispatched",
                    )
                )
            if step.effective_risk not in _RISK_VALUES:
                step_errors.append(
                    self._error(
                        CompileErrorCode.RISK_INVALID,
                        step,
                        f"effective_risk {step.effective_risk!r} is not a RiskTier",
                    )
                )
            elif step.effective_risk == RiskTier.IRREVERSIBLE.value:
                step_errors.append(
                    self._error(
                        CompileErrorCode.IRREVERSIBLE_STEP,
                        step,
                        "an IRREVERSIBLE action is never auto-dispatched and cannot be compiled "
                        "into a replayable step",
                    )
                )
            if step_errors:
                errors += step_errors
                continue

            action = ActionType(step.action_type)
            if action is ActionType.NAVIGATE:
                item, step_errors = self._compile_navigate(step, declaration)
            else:
                item, step_errors = self._compile_targeted(step, action, declaration)
            errors += step_errors
            if item is not None:
                compiled.append(item)
        return compiled, errors

    def _compile_navigate(
        self, step: TraceStepSummary, declaration: CapabilityDeclaration
    ) -> tuple[_Compiled | None, list[CompileError]]:
        errors: list[CompileError] = []
        route = step.route_template
        if route is None:
            errors.append(
                self._error(CompileErrorCode.ROUTE_MISSING, step, "NAVIGATE has no route_template")
            )
        elif not _is_path(route):
            errors.append(
                self._error(
                    CompileErrorCode.ROUTE_INVALID,
                    step,
                    f"route_template {route!r} is not a path",
                    field="route_template",
                )
            )
        landed, landing_errors = self._landing_path(step)
        errors += landing_errors
        if errors:
            return None, errors
        assert route is not None and landed is not None
        step_id = f"s{step.step_index}_navigate"
        compiled = {
            "step_id": step_id,
            "description": f"Open {route}.",
            "action": ActionType.NAVIGATE.value,
            "route": route,
            "risk": step.effective_risk,
            "postcondition": RouteMatches(route=landed).model_dump(),
        }
        mapping = StepMapping(
            step_index=step.step_index,
            step_id=step_id,
            action=ActionType.NAVIGATE.value,
            postcondition_rule=POSTCONDITION_ROUTE_RULE,
            route_changed=_path_of(step.url_before_template) != landed,
            risk=step.effective_risk,
            risk_source=RISK_SOURCE,
        )
        return _Compiled(compiled, mapping), errors

    def _compile_targeted(
        self, step: TraceStepSummary, action: ActionType, declaration: CapabilityDeclaration
    ) -> tuple[_Compiled | None, list[CompileError]]:
        errors: list[CompileError] = []
        descriptor = self._descriptor(step, errors)
        binding = self._binding(step, action, declaration, errors)
        if action is ActionType.READ:
            self._check_read(step, declaration, errors)
        postcondition: Condition | None = None
        rule: str | None = None
        route_changed: bool | None = None
        if action in _VALUE_ACTIONS:
            if descriptor is not None and binding is not None:
                postcondition = ValueEquals(target=descriptor, value=binding)
                rule = POSTCONDITION_VALUE_RULE
        elif action in _ROUTE_EFFECT_ACTIONS:
            landed, landing_errors = self._landing_path(step)
            errors += landing_errors
            if landed is not None:
                postcondition = RouteMatches(route=landed)
                rule = POSTCONDITION_ROUTE_RULE
                route_changed = _path_of(step.url_before_template) != landed
        if errors:
            return None, errors
        assert descriptor is not None and step.target is not None

        step_id = f"s{step.step_index}_{action.value.lower()}"
        if action is ActionType.READ:
            step_id += f"_{step.output_name}"
        compiled = {
            "step_id": step_id,
            "description": self._describe(step, action, declaration),
            "action": action.value,
            "target": descriptor.model_dump(),
            "risk": step.effective_risk,
        }
        if binding is not None:
            compiled["value"] = binding.model_dump()
        if action is ActionType.READ:
            compiled["output"] = step.output_name
        else:
            assert postcondition is not None
            compiled["postcondition"] = postcondition.model_dump()
        mapping = StepMapping(
            step_index=step.step_index,
            step_id=step_id,
            action=action.value,
            target_rule=TARGET_RULE,
            target_identity_matches=step.identity_matches,
            binding_rule=None
            if binding is None
            else f"preserved from the trace: {step.value_binding_kind}",
            postcondition_rule=rule,
            route_changed=route_changed,
            risk=step.effective_risk,
            risk_source=RISK_SOURCE,
        )
        return _Compiled(compiled, mapping), errors

    def _descriptor(
        self, step: TraceStepSummary, errors: list[CompileError]
    ) -> TargetDescriptor | None:
        target = step.target
        if target is None:
            errors.append(
                self._error(
                    CompileErrorCode.TARGET_MISSING,
                    step,
                    f"{step.action_type} has no semantic target",
                    field="target",
                )
            )
            return None
        if step.identity_matches is None:
            errors.append(
                self._error(
                    CompileErrorCode.TARGET_IDENTITY_UNPROVEN,
                    step,
                    "identity_matches is absent; the descriptor was never proven unique",
                    field="identity_matches",
                )
            )
        elif step.identity_matches != 1:
            errors.append(
                self._error(
                    CompileErrorCode.TARGET_AMBIGUOUS,
                    step,
                    f"identity_matches={step.identity_matches}; exactly 1 is required "
                    "(never first())",
                    field="identity_matches",
                )
            )
        for name, value in (
            ("role", target.role),
            ("accessible_name", target.accessible_name),
            ("context_hint", target.context_hint),
        ):
            self._check_durable(value, f"target.{name}", step, errors)
        if step.read_text and any(
            step.read_text in value
            for value in (target.role, target.accessible_name, target.context_hint)
            if value
        ):
            errors.append(
                self._error(
                    CompileErrorCode.READ_VALUE_IN_TARGET,
                    step,
                    "the observed read value appears in the target identity; a value is not "
                    "a durable identity",
                    field="target",
                )
            )
        if errors:
            return None
        return self._strategy_of(target)

    @staticmethod
    def _strategy_of(target: SemanticTargetSummary) -> TargetDescriptor:
        return TargetDescriptor(
            strategies=[
                TargetStrategy(
                    role=target.role,
                    name=target.accessible_name,
                    scope=target.context_hint,
                )
            ]
        )

    def _binding(
        self,
        step: TraceStepSummary,
        action: ActionType,
        declaration: CapabilityDeclaration,
        errors: list[CompileError],
    ) -> InputRef | LiteralValue | None:
        kind = step.value_binding_kind
        if action not in _VALUE_ACTIONS:
            if kind is not None or step.input_name is not None or step.literal_value is not None:
                errors.append(
                    self._error(
                        CompileErrorCode.BINDING_INVALID,
                        step,
                        f"{action.value} must not carry a value binding",
                        field="value_binding_kind",
                    )
                )
            return None
        if kind is None:
            errors.append(
                self._error(
                    CompileErrorCode.BINDING_MISSING,
                    step,
                    f"{action.value} has no value binding",
                    field="value_binding_kind",
                )
            )
            return None
        if kind == "INPUT_REF":
            if not step.input_name:
                errors.append(
                    self._error(
                        CompileErrorCode.BINDING_INVALID,
                        step,
                        "INPUT_REF without input_name",
                        field="input_name",
                    )
                )
                return None
            if step.input_name not in declaration.inputs:
                errors.append(
                    self._error(
                        CompileErrorCode.INPUT_UNDECLARED,
                        step,
                        f"INPUT_REF {step.input_name!r} is not a declared input",
                        field="input_name",
                    )
                )
                return None
            return InputRef(input_name=step.input_name)
        if kind == "LITERAL":
            if step.literal_value is None:
                errors.append(
                    self._error(
                        CompileErrorCode.BINDING_INVALID,
                        step,
                        "LITERAL without literal_value",
                        field="literal_value",
                    )
                )
                return None
            if placeholders(step.literal_value) or _MODEL_PLACEHOLDER in step.literal_value:
                errors.append(
                    self._error(
                        CompileErrorCode.BINDING_INVALID,
                        step,
                        "a LITERAL must be plain text, not a placeholder",
                        field="literal_value",
                    )
                )
                return None
            return LiteralValue(value=step.literal_value)
        errors.append(
            self._error(
                CompileErrorCode.BINDING_INVALID,
                step,
                f"unknown value_binding_kind {kind!r}",
                field="value_binding_kind",
            )
        )
        return None

    def _check_read(
        self,
        step: TraceStepSummary,
        declaration: CapabilityDeclaration,
        errors: list[CompileError],
    ) -> None:
        name = step.output_name
        if not name or name not in declaration.outputs:
            errors.append(
                self._error(
                    CompileErrorCode.OUTPUT_UNDECLARED,
                    step,
                    f"READ produces {name!r}, not a declared output "
                    f"({sorted(declaration.outputs)})",
                    field="output_name",
                )
            )
            return
        transform = declaration.outputs[name].type
        if step.read_text is None:
            errors.append(
                self._error(
                    CompileErrorCode.OUTPUT_TRANSFORM_INCONSISTENT,
                    step,
                    "READ recorded no observed text",
                    field="read_text",
                )
            )
            return
        try:
            validate_output(transform, apply_transform(transform, step.read_text))
        except TransformError as exc:
            errors.append(
                self._error(
                    CompileErrorCode.OUTPUT_TRANSFORM_INCONSISTENT,
                    step,
                    f"observed text does not convert under {transform.value}: {exc}",
                    field="read_text",
                )
            )

    def _landing_path(self, step: TraceStepSummary) -> tuple[str | None, list[CompileError]]:
        landed = _path_of(step.url_after_template)
        if not _is_path(landed):
            return None, [
                self._error(
                    CompileErrorCode.ROUTE_INVALID,
                    step,
                    f"url_after_template {step.url_after_template!r} has no path",
                    field="url_after_template",
                )
            ]
        return landed, []

    @staticmethod
    def _describe(
        step: TraceStepSummary, action: ActionType, declaration: CapabilityDeclaration
    ) -> str:
        target = step.target
        assert target is not None
        label = target.role
        if target.accessible_name:
            label = f"{target.role} '{target.accessible_name}'"
        where = f" in '{target.context_hint}'" if target.context_hint else ""
        if action is ActionType.READ:
            transform = declaration.outputs[step.output_name or ""].type.value
            return f"Read the {label}{where} as {step.output_name} ({transform})."
        if action in _VALUE_ACTIONS:
            verb = "Fill" if action is ActionType.FILL else "Select in"
            if step.value_binding_kind == "INPUT_REF":
                return f"{verb} the {label}{where} with input {step.input_name}."
            return f"{verb} the {label}{where} with the literal '{step.literal_value}'."
        return f"Click the {label}{where}."

    # --- artifact-level checks -------------------------------------------------------------------

    def _check_inputs(
        self,
        steps: list[TraceStepSummary],
        compiled: list[_Compiled],
        declaration: CapabilityDeclaration,
    ) -> list[CompileError]:
        errors: list[CompileError] = []
        action_sites = self._action_sites(compiled)
        for name, spec in declaration.inputs.items():
            if spec.required and not action_sites.get(name):
                errors.append(
                    CompileError(
                        code=CompileErrorCode.INPUT_NOT_PARAMETERIZED,
                        field=name,
                        message=(
                            f"required input {name!r} drives no action (no INPUT_REF on a "
                            "FILL/SELECT and no {placeholder} in a NAVIGATE route): the value "
                            "was hard-coded into the actions"
                        ),
                    )
                )
        for step in steps:
            if step.action_type != ActionType.READ.value:
                continue
            page = _path_of(step.url_before_template)
            for name, kind in step.input_evidence.items():
                if name not in declaration.inputs:
                    errors.append(
                        self._error(
                            CompileErrorCode.INPUT_UNDECLARED,
                            step,
                            f"input_evidence names undeclared input {name!r}",
                            field="input_evidence",
                        )
                    )
                elif kind == ROUTE_EVIDENCE and f"{{{name}}}" not in _segments(page):
                    errors.append(
                        self._error(
                            CompileErrorCode.TRACE_NOT_TEMPLATED,
                            step,
                            f"the read page evidenced {name!r} by ROUTE but {page!r} carries no "
                            f"{{{name}}} segment: the runtime value survived untemplated",
                            field="url_before_template",
                        )
                    )
        return errors

    @staticmethod
    def _action_sites(compiled: list[_Compiled]) -> dict[str, list[str]]:
        sites: dict[str, list[str]] = {}
        for item in compiled:
            step = item.step
            value = step.get("value")
            if value and value.get("kind") == "INPUT_REF":
                sites.setdefault(value["input_name"], []).append(step["step_id"])
            route = step.get("route")
            if route:
                for name in placeholders(route):
                    sites.setdefault(name, []).append(step["step_id"])
        return sites

    def _map_outputs(
        self,
        steps: list[TraceStepSummary],
        compiled: list[_Compiled],
        declaration: CapabilityDeclaration,
    ) -> tuple[dict[str, OutputMapping], list[CompileError]]:
        errors: list[CompileError] = []
        producers: dict[str, list[str]] = {}
        for item in compiled:
            output = item.step.get("output")
            if output:
                producers.setdefault(output, []).append(item.step["step_id"])
        for name, step_ids in producers.items():
            if len(step_ids) > 1:
                errors.append(
                    CompileError(
                        code=CompileErrorCode.OUTPUT_DUPLICATE_PRODUCER,
                        field=name,
                        message=f"output {name!r} is produced by {step_ids}; exactly one READ",
                    )
                )
        for name in declaration.outputs:
            if name not in producers:
                errors.append(
                    CompileError(
                        code=CompileErrorCode.OUTPUT_NOT_PRODUCED,
                        field=name,
                        message=f"declared output {name!r} is produced by no READ step",
                    )
                )
        if errors:
            return {}, errors
        return {
            name: OutputMapping(
                type=declaration.outputs[name].type.value,
                producer_step_id=step_ids[0],
                read_text_converts_under_transform=True,
            )
            for name, step_ids in producers.items()
        }, errors

    def _derive_checkpoint(
        self, steps: list[TraceStepSummary], declaration: CapabilityDeclaration
    ) -> tuple[CheckpointDerivation | None, list[CompileError]]:
        final = _path_of(steps[-1].url_after_template)
        if not _is_path(final):
            return None, [
                self._error(
                    CompileErrorCode.SUCCESS_SEMANTICS_UNDERIVABLE,
                    steps[-1],
                    f"the final observed url {steps[-1].url_after_template!r} has no path",
                    field="url_after_template",
                )
            ]
        reads = [
            s
            for s in steps
            if s.action_type == ActionType.READ.value and s.output_name in declaration.outputs
        ]
        if not reads:
            return None, [
                CompileError(
                    code=CompileErrorCode.SUCCESS_SEMANTICS_MISSING,
                    message="no READ of a declared output to derive success semantics from",
                )
            ]
        last_read = reads[-1]
        read_page = _path_of(last_read.url_before_template)
        if read_page != final:
            return None, [
                self._error(
                    CompileErrorCode.SUCCESS_SEMANTICS_UNDERIVABLE,
                    last_read,
                    f"the outputs were read on {read_page!r} but the run ended on {final!r}; "
                    "the read-page proof cannot be expressed as a final checkpoint",
                    field="url_before_template",
                )
            ]
        required = [name for name, spec in declaration.inputs.items() if spec.required]
        evidence_kind: str | None = None
        parameterized_by: list[str] = []
        if required:
            if not last_read.input_evidence:
                return None, [
                    self._error(
                        CompileErrorCode.SUCCESS_SEMANTICS_MISSING,
                        last_read,
                        "the page the outputs were read from evidenced no declared input; a "
                        "checkpoint that holds for any input is not a success proof",
                        field="input_evidence",
                    )
                ]
            route_inputs = [
                name
                for name in required
                if last_read.input_evidence.get(name) == ROUTE_EVIDENCE
                and f"{{{name}}}" in _segments(final)
            ]
            if not route_inputs:
                return None, [
                    self._error(
                        CompileErrorCode.SUCCESS_SEMANTICS_UNDERIVABLE,
                        last_read,
                        f"input_evidence {last_read.input_evidence} proves no required input by "
                        "ROUTE; the only derivable checkpoint condition is a route, and no "
                        "heading/title condition can be derived from the trace",
                        field="input_evidence",
                    )
                ]
            evidence_kind = ROUTE_EVIDENCE
            parameterized_by = route_inputs
        return CheckpointDerivation(
            rule=CHECKPOINT_RULE,
            conditions=[RouteMatches(route=final)],
            parameterized_by=parameterized_by,
            evidence_kind=evidence_kind,
        ), []

    def _check_known_outcomes(self, declaration: CapabilityDeclaration) -> list[CompileError]:
        errors: list[CompileError] = []
        for outcome in declaration.known_outcomes:
            for leaf in _string_leaves(outcome.detector.model_dump()):
                problem = _durable_problem(leaf)
                if problem is not None:
                    errors.append(
                        CompileError(
                            code=CompileErrorCode.KNOWN_OUTCOME_INVALID,
                            field=outcome.code,
                            message=f"detector value {leaf!r}: {problem[1]}",
                        )
                    )
        return errors

    def _scan_durable_strings(
        self, assembled: dict, declaration: CapabilityDeclaration
    ) -> list[CompileError]:
        """I1 over the whole assembled artifact: no ref, selector or model placeholder survives,
        and every ``{placeholder}`` names a declared input."""
        errors: list[CompileError] = []
        for leaf in _string_leaves(assembled):
            problem = _durable_problem(leaf)
            if problem is not None:
                code, why = problem
                errors.append(CompileError(code=code, message=f"{leaf!r}: {why}"))
            unknown = placeholders(leaf) - set(declaration.inputs)
            if unknown:
                errors.append(
                    CompileError(
                        code=CompileErrorCode.INPUT_UNDECLARED,
                        message=f"{leaf!r} references undeclared inputs {sorted(unknown)}",
                    )
                )
        return errors

    # --- report ----------------------------------------------------------------------------------

    @staticmethod
    def _input_mappings(
        artifact: CapabilityArtifact, declaration: CapabilityDeclaration
    ) -> dict[str, InputMapping]:
        action_sites: dict[str, list[str]] = {name: [] for name in declaration.inputs}
        observation_sites: dict[str, list[str]] = {name: [] for name in declaration.inputs}
        for step in artifact.steps:
            if isinstance(step.value, InputRef):
                action_sites[step.value.input_name].append(step.step_id)
            if step.route:
                for name in placeholders(step.route):
                    action_sites[name].append(step.step_id)
            if step.target:
                for name in step.target.placeholders():
                    observation_sites[name].append(f"{step.step_id}.target")
            if step.postcondition:
                names = _condition_placeholders(step.postcondition) | _condition_input_refs(
                    step.postcondition
                )
                for name in names:
                    observation_sites[name].append(f"{step.step_id}.postcondition")
        for index, condition in enumerate(artifact.success_checkpoint):
            for name in _condition_placeholders(condition) | _condition_input_refs(condition):
                observation_sites[name].append(f"success_checkpoint[{index}]")
        for outcome in artifact.known_outcomes:
            names = _condition_placeholders(outcome.detector) | _condition_input_refs(
                outcome.detector
            )
            for name in names:
                observation_sites[name].append(f"known_outcomes.{outcome.code}")
        return {
            name: InputMapping(
                type=spec.type.value,
                required=spec.required,
                action_sites=action_sites[name],
                observation_sites=observation_sites[name],
            )
            for name, spec in declaration.inputs.items()
        }

    @staticmethod
    def _invariants(
        artifact: CapabilityArtifact,
        compiled: list[_Compiled],
        checkpoint: CheckpointDerivation,
        inputs: dict[str, InputMapping],
    ) -> list[InvariantCheck]:
        leaves = list(_string_leaves(artifact.model_dump(mode="json")))
        targeted = [m.mapping for m in compiled if m.mapping.target_identity_matches is not None]
        return [
            InvariantCheck(
                invariant="I1",
                statement="no transient ref, selector, code or model placeholder in any field",
                evidence=[
                    f"{len(leaves)} string fields scanned: 0 ref-shaped, 0 selector-prefixed, "
                    "0 model-style placeholder tokens",
                    "TargetStrategy fields are role/name/scope/text_contains only",
                ],
            ),
            InvariantCheck(
                invariant="I2",
                statement="every required input drives an action as INPUT_REF or a route "
                "placeholder; no bound value was received or replaced",
                evidence=[
                    f"{name}: action sites {mapping.action_sites}, observation sites "
                    f"{mapping.observation_sites}"
                    for name, mapping in inputs.items()
                ],
            ),
            InvariantCheck(
                invariant="I3",
                statement="persisted actions only, in trace order, every one gate-ALLOWed",
                evidence=[
                    " -> ".join(f"{m.mapping.step_index}:{m.mapping.action}" for m in compiled),
                ],
            ),
            InvariantCheck(
                invariant="I4",
                statement="each ref-targeted step compiles the descriptor the ambiguity guard "
                "resolved to exactly one element; one strategy, no fallback, no first()",
                evidence=[
                    f"{m.step_id}: identity_matches={m.target_identity_matches}" for m in targeted
                ],
            ),
            InvariantCheck(
                invariant="I5",
                statement="each declared output has exactly one producing READ whose observed "
                "text converts under the declared transform; the checkpoint is derived from the "
                "final observed route and parameterized by ROUTE-evidenced inputs",
                evidence=[
                    f"outputs: {sorted(artifact.outputs)}",
                    f"checkpoint parameterized by {checkpoint.parameterized_by} "
                    f"(evidence {checkpoint.evidence_kind})",
                ],
            ),
            InvariantCheck(
                invariant="I6",
                statement="provenance names the discovery run, the model and the compiler; "
                "descriptions are generated; known outcomes are DECLARED",
                evidence=[
                    f"source=discovery run={artifact.provenance.discovery_run_id} "
                    f"model={artifact.provenance.model_id} "
                    f"compiler={artifact.provenance.compiler_version}",
                ],
            ),
        ]

    @staticmethod
    def _error(
        code: CompileErrorCode, step: TraceStepSummary, message: str, *, field: str | None = None
    ) -> CompileError:
        return CompileError(code=code, message=message, step_index=step.step_index, field=field)

    @classmethod
    def _check_durable(
        cls, value: str | None, field: str, step: TraceStepSummary, errors: list[CompileError]
    ) -> None:
        if value is None:
            return
        problem = _durable_problem(value)
        if problem is None:
            return
        code, why = problem
        errors.append(cls._error(code, step, f"{value!r}: {why}", field=field))


def _durable_problem(value: str) -> tuple[CompileErrorCode, str] | None:
    """Why ``value`` may not be persisted in an artifact, if it may not (I1)."""
    if _TRANSIENT_REF.match(value):
        return CompileErrorCode.TRANSIENT_REF, "looks like a transient observation ref"
    if value.startswith(_SELECTOR_PREFIXES):
        return CompileErrorCode.SELECTOR_SYNTAX, "looks like a selector, not a semantic"
    if _MODEL_PLACEHOLDER in value:
        return (
            CompileErrorCode.TRACE_NOT_TEMPLATED,
            "carries a model-style placeholder; durable fields use {name}",
        )
    return None


def _string_leaves(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _string_leaves(value)
    elif isinstance(node, list | tuple):
        for value in node:
            yield from _string_leaves(value)

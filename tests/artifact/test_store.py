import json

import pytest
from pydantic import ValidationError

from cua.artifact import ArtifactConflict, ArtifactNotFound, ArtifactStore, CapabilityArtifact
from tests.artifact.test_schema import build


def test_save_then_load_round_trips(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    artifact = build()
    path = store.save(artifact)
    assert path.name == "demo@0.1.0.json"
    assert store.load_id("demo@0.1.0") == artifact
    assert store.load("demo", "0.1.0") == artifact
    assert store.list_ids() == ["demo@0.1.0"]


def test_saved_file_is_readable_json_with_plain_strings(tmp_path):
    store = ArtifactStore(tmp_path)
    store.save(build())
    data = json.loads((tmp_path / "demo@0.1.0.json").read_text())
    assert data["steps"][1]["value"] == {"kind": "INPUT_REF", "input_name": "member_id"}
    assert data["provenance"]["source"] == "handwritten"


def test_unknown_id_raises(tmp_path):
    with pytest.raises(ArtifactNotFound):
        ArtifactStore(tmp_path).load_id("nope@1.0.0")


def test_path_traversal_and_malformed_ids_are_rejected(tmp_path):
    store = ArtifactStore(tmp_path)
    for bad in ("../x@1.0.0", "demo", "a/b@1.0.0"):
        with pytest.raises(ValueError):
            store.path_for(bad)


def test_tampered_file_fails_validation_on_load(tmp_path):
    store = ArtifactStore(tmp_path)
    path = store.save(build())
    data = json.loads(path.read_text())
    data["steps"][2]["target"]["strategies"][0]["name"] = "e30"  # transient ref smuggled in
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        store.load_id("demo@0.1.0")


def test_file_claiming_a_different_identity_is_rejected(tmp_path):
    store = ArtifactStore(tmp_path)
    path = store.save(build())
    (tmp_path / "other@0.1.0.json").write_text(path.read_text())
    with pytest.raises(ValueError, match="claims to be"):
        store.load_id("other@0.1.0")


def test_same_identity_different_content_is_refused(tmp_path):
    store = ArtifactStore(tmp_path)
    store.save(build())

    def mutate(d):
        d["description"] = "changed without a version bump"

    with pytest.raises(ArtifactConflict):
        store.save(build(mutate))
    # Re-saving identical content is fine.
    store.save(build())
    assert (
        CapabilityArtifact.model_validate_json((tmp_path / "demo@0.1.0.json").read_text())
        == build()
    )


def test_compile_report_lives_beside_the_artifact_and_is_never_listed_as_one(tmp_path):
    from cua.artifact.compile_report import CompileSuccess
    from cua.discovery.capabilities import READ_SAVINGS_BALANCE_DECLARATION
    from tests.artifact.compiler_support import compile_official

    result = compile_official(READ_SAVINGS_BALANCE_DECLARATION)
    assert isinstance(result, CompileSuccess)
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError, match="report is for"):
        store.save_compile_report("other@1.0.0", result.report)
    path = store.save_compile_report(result.artifact.artifact_id, result.report)
    assert path == tmp_path / "compile_reports" / "read_savings_balance@1.0.0.json"
    assert store.list_ids() == []
    assert store.load_compile_report(result.artifact.artifact_id) == result.report
    with pytest.raises(ArtifactNotFound):
        store.load_compile_report("nope@1.0.0")

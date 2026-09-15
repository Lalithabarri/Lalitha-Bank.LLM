"""ArtifactStore — validated artifacts on disk, identified by ``name@version`` (ARCHITECTURE §10).

One JSON file per artifact under a root directory; the file name *is* the identity. Nothing is
written or returned without passing ``CapabilityArtifact`` validation, and an existing identity
is never silently overwritten with different content.
"""

import json
from pathlib import Path

from cua.artifact.schema import CapabilityArtifact


class ArtifactNotFound(FileNotFoundError):
    pass


class ArtifactConflict(FileExistsError):
    pass


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, artifact_id: str) -> Path:
        if "/" in artifact_id or "\\" in artifact_id or "@" not in artifact_id:
            raise ValueError(f"not an artifact id: {artifact_id!r}")
        return self._root / f"{artifact_id}.json"

    def save(self, artifact: CapabilityArtifact) -> Path:
        path = self.path_for(artifact.artifact_id)
        payload = (
            json.dumps(json.loads(artifact.model_dump_json()), indent=2, ensure_ascii=False) + "\n"
        )
        if path.exists() and path.read_text() != payload:
            existing = CapabilityArtifact.model_validate_json(path.read_text())
            if existing != artifact:
                raise ArtifactConflict(
                    f"{artifact.artifact_id} already exists with different content; "
                    "bump capability_version instead of overwriting"
                )
        self._root.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
        return path

    def load_id(self, artifact_id: str) -> CapabilityArtifact:
        path = self.path_for(artifact_id)
        if not path.exists():
            raise ArtifactNotFound(str(path))
        artifact = CapabilityArtifact.model_validate_json(path.read_text())
        if artifact.artifact_id != artifact_id:
            raise ValueError(
                f"file {path.name} claims to be {artifact.artifact_id!r}, not {artifact_id!r}"
            )
        return artifact

    def load(self, capability_name: str, capability_version: str) -> CapabilityArtifact:
        return self.load_id(f"{capability_name}@{capability_version}")

    def list_ids(self) -> list[str]:
        if not self._root.exists():
            return []
        return sorted(p.stem for p in self._root.glob("*@*.json"))

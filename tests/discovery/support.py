"""Shared fixtures for the discovery tests: the production composition over the scripted surface,
the flagship goal, and snapshots built from the Milestone 2 captures."""

from __future__ import annotations

from cua.discovery import DiscoveryAgent, DiscoveryConfig, DiscoveryDeps
from cua.domain import SurfaceSnapshot
from cua.evidence import EvidenceRecorder
from cua.hitl import ControlOwner
from cua.policy import ActionGate, PolicyConfig
from cua.surface.aria import read_snapshot
from tests.replay.fake_clock import FakeClock
from tests.replay.scripted_surface import ScriptedSurface, fixture_text
from tests.replay.support import policy_for, recorder_for

BASE_URL = "http://fake.test"
ENTRY_ROUTES = ("/members/search",)


def snapshot_from_fixture(
    name: str, *, url: str, step_index: int = 1, title: str | None = None
) -> SurfaceSnapshot:
    elements, outline = read_snapshot(fixture_text(name))
    return SurfaceSnapshot(
        url=url,
        page_title=title or f"{name} - LegacyBank Operations Console",
        step_index=step_index,
        elements=elements,
        visible_text_outline=outline,
    )


def search_snapshot(step_index: int = 1) -> SurfaceSnapshot:
    return snapshot_from_fixture(
        "A_search",
        url=f"{BASE_URL}/members/search",
        step_index=step_index,
        title="Member Search - LegacyBank Operations Console",
    )


def detail_snapshot(step_index: int = 3, *, ambiguous: bool = False) -> SurfaceSnapshot:
    name = "G_detail_M1001_ambiguous" if ambiguous else "B_detail_M1001"
    return snapshot_from_fixture(
        name,
        url=f"{BASE_URL}/members/M1001",
        step_index=step_index,
        title="Member M1001 - LegacyBank Operations Console",  # as the real app titles it
    )


def not_found_snapshot(step_index: int = 3) -> SurfaceSnapshot:
    return snapshot_from_fixture(
        "F_not_found",
        url=f"{BASE_URL}/members/search",
        step_index=step_index,
        title="Member Search - LegacyBank Operations Console",
    )


def agent_for(
    surface,
    llm,
    *,
    clock: FakeClock | None = None,
    base_url: str = BASE_URL,
    policy: PolicyConfig | None = None,
    owner: ControlOwner | None = None,
    recorder: EvidenceRecorder | None = None,
    **config_overrides,
) -> tuple[DiscoveryAgent, EvidenceRecorder, FakeClock]:
    """The production composition: one recorder shared by the surface, the gate and the agent.

    The *surface* must be wired to that recorder (``listener=recorder``); the agent's
    dispatch-count invariant fails loudly otherwise.
    """
    recorder = recorder or recorder_for()
    clock = clock or FakeClock()
    gate = ActionGate(policy or policy_for(base_url), owner or ControlOwner(), observer=recorder)
    config = DiscoveryConfig(
        base_url=base_url,
        navigation_routes=config_overrides.pop("navigation_routes", ENTRY_ROUTES),
        **config_overrides,
    )
    agent = DiscoveryAgent(
        DiscoveryDeps(surface=surface, action_gate=gate, clock=clock, evidence=recorder, llm=llm),
        config,
    )
    return agent, recorder, clock


def scripted_surface(recorder: EvidenceRecorder, **kwargs) -> ScriptedSurface:
    return ScriptedSurface(base_url=BASE_URL, listener=recorder, **kwargs)

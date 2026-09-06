"""PDDL action fragments derived from capability manifests.

The toolbox publishes fragments; the agentic core composes them into a domain
and generates problems. The planner is never invoked here.
"""

from __future__ import annotations

from collections.abc import Sequence

from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry

# A single type keeps the toolbox domain simple; the agentic core can extend it.
DEFAULT_TYPES = "document artifact"


def capability_action(manifest: CapabilityManifest) -> str:
    """Render a single PDDL action from a capability manifest.

    The action name is derived from the capability ID, replacing dots with
    hyphens so it stays valid PDDL syntax.
    """
    parameters = ", ".join(
        f"?{_pddl_param(param)} - {_pddl_type(param)}"
        for param in manifest.semantics.requires
    )
    if not parameters:
        parameters = "?d - document"

    requires = "\n      ".join(
        f"({predicate})" for predicate in manifest.semantics.requires
    ) or "(document-available ?d)"

    produces = "\n      ".join(
        f"({predicate})" for predicate in manifest.semantics.produces
    ) or "(processed ?d)"

    return f"""  (:action {_action_name(manifest.id)}
    :parameters ({parameters})
    :precondition
      (and
        {requires})
    :effect
      (and
        {produces}))"""


def domain_fragment(manifests: Sequence[CapabilityManifest]) -> str:
    """Build a domain fragment from a list of capability manifests.

    The fragment is self-contained: types, predicates and actions derived
    solely from the manifests. The agentic core merges it into a larger domain.
    """
    predicates: set[str] = set()
    actions: list[str] = []

    for manifest in manifests:
        predicates.update(manifest.semantics.requires)
        predicates.update(manifest.semantics.produces)
        actions.append(capability_action(manifest))

    predicates_list = sorted(predicates) if predicates else ["document-available"]
    types_line = f"    {DEFAULT_TYPES}" if DEFAULT_TYPES else ""

    return f"""(define (domain acessilia-toolbox-fragment)
  (:requirements :strips)
  (:types{types_line})
  (:predicates
    {chr(10).join(f'    ({p})' for p in predicates_list)})
{chr(10).join(actions)}
)"""


def predicates_list(capabilities: CapabilityRegistry) -> list[str]:
    """All predicates across registered capabilities."""
    predicates: set[str] = set()
    for manifest in capabilities.manifests():
        predicates.update(manifest.semantics.requires)
        predicates.update(manifest.semantics.produces)
    return sorted(predicates) if predicates else ["document-available"]


def _action_name(capability_id: str) -> str:
    return capability_id.replace(".", "-")


def _pddl_param(predicate: str) -> str:
    return predicate.split("_")[0] if "_" in predicate else "d"


def _pddl_type(predicate: str) -> str:
    return "document"

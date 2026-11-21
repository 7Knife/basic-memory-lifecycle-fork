"""Lifecycle service (deprecated, unused in shipped MCP paths).

`LifecycleService` and its metadata-application helpers have no callers outside
this module. The shipped lifecycle tools use the API `set_metadata` path instead
(`KnowledgeClient.patch_entity`); these EntityService-based methods remain only
as legacy internal helpers and may be removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from basic_memory.lifecycle import (
    CANON_ARCHIVED,
    FIELD_CANON_STATUS,
    ReviewQueue,
    decay_update,
    hit_bonus_update,
)
from basic_memory.services.entity_service import EntityService


@dataclass
class LifecycleChange:
    """Legacy lifecycle metadata change result; not used by shipped MCP tools."""

    permalink: str
    before: dict[str, Any]
    after: dict[str, Any]
    archived: bool = False


class LifecycleService:
    """Deprecated EntityService-based lifecycle writer with no shipped callers."""

    def __init__(self, entity_service: EntityService, review_queue: ReviewQueue | None = None):
        self.entities = entity_service
        self.reviews = review_queue or ReviewQueue()

    async def apply_metadata(self, permalink: str, update: dict[str, Any]) -> LifecycleChange:
        """Deprecated. Use the API `set_metadata` path through the shipped MCP tools."""
        entity = await self.entities.link_resolver.resolve_link(permalink, strict=True, load_relations=False)
        if not entity:
            raise ValueError(f"Entity not found: {permalink}")
        before = {key: (entity.entity_metadata or {}).get(key) for key in update}
        clean = {key: value for key, value in update.items() if value is not None}
        if not clean:
            return LifecycleChange(permalink, before, {})
        await self.entities.edit_entity_with_content(
            identifier=permalink,
            operation="set_metadata",
            content="",
            metadata=clean,
        )
        archived = str(clean.get(FIELD_CANON_STATUS, "")) == CANON_ARCHIVED
        return LifecycleChange(permalink, before, clean, archived)

    async def decay_entity(self, permalink: str, *, now=None, force: bool = False) -> LifecycleChange | None:
        """Deprecated legacy decay helper."""
        entity = await self.entities.link_resolver.resolve_link(permalink, strict=True, load_relations=False)
        if not entity:
            return None
        update = decay_update(dict(entity.entity_metadata or {}), now=now, force=force)
        if not update:
            return None
        return await self.apply_metadata(permalink, update)

    async def record_hit(self, permalink: str, *, now=None) -> LifecycleChange | None:
        """Deprecated legacy hit helper; use `bm_memory_review_hit` instead."""
        entity = await self.entities.link_resolver.resolve_link(permalink, strict=True, load_relations=False)
        if not entity:
            return None
        update = hit_bonus_update(dict(entity.entity_metadata or {}), now=now)
        if not update:
            return None
        return await self.apply_metadata(permalink, update)

    async def apply_proposal(self, proposal_id: str) -> LifecycleChange | None:
        """Deprecated proposal application helper.

        Kept for compatibility; new notes still need `bm_write`, and the shipped
        review tools use `bm_edit(command='set_metadata')`.
        """
        proposal = self.reviews.get(proposal_id)
        if proposal is None:
            return None
        if proposal.status != "approved":
            logger.warning(f"Proposal {proposal_id} is not approved")
            return None
        if proposal.operation == "new":
            return None
        if not proposal.permalink:
            return None
        meta = ReviewQueue.applies_to_metadata(proposal)
        if not meta:
            return None
        change = await self.apply_metadata(proposal.permalink, meta)
        self.reviews.mark_applied(proposal_id)
        return change


__all__ = ["LifecycleService", "LifecycleChange"]

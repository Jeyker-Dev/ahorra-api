from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.sync.domain import SyncEntity, Tombstone
from app.sync.models import SyncTombstoneModel


def _tombstone_to_domain(model: SyncTombstoneModel) -> Tombstone:
    return Tombstone(
        entity=SyncEntity(model.entity),
        id=model.id,
        deleted_at=model.deleted_at,
    )


class SqlAlchemySyncTombstoneRepository:
    """SQLAlchemy implementation of SyncTombstoneRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_changed_between(
        self,
        since: datetime | None,
        until: datetime,
    ) -> list[Tombstone]:
        stmt = select(SyncTombstoneModel).where(SyncTombstoneModel.deleted_at <= until)
        if since is not None:
            stmt = stmt.where(SyncTombstoneModel.deleted_at > since)
        stmt = stmt.order_by(
            SyncTombstoneModel.deleted_at.asc(),
            SyncTombstoneModel.entity.asc(),
            SyncTombstoneModel.id.asc(),
        )
        result = await self._session.execute(stmt)
        return [_tombstone_to_domain(model) for model in result.scalars().all()]

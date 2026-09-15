from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key"),)

    device_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        index=True,
    )
    idempotency_key: Mapped[UUID] = mapped_column()

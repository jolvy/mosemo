import unicodedata
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base


@dataclass(frozen=True, slots=True)
class DefaultLabel:
    display_name: str
    default_key: str


DEFAULT_LABELS: tuple[DefaultLabel, ...] = (
    DefaultLabel(display_name="코딩", default_key="coding"),
    DefaultLabel(display_name="학습", default_key="learning"),
    DefaultLabel(display_name="소통", default_key="communication"),
    DefaultLabel(display_name="쇼핑", default_key="shopping"),
    DefaultLabel(display_name="여가", default_key="leisure"),
)


def normalize_label_name(display_name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", display_name).casefold().split())


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (
        UniqueConstraint("account_id", "name_key"),
        UniqueConstraint("account_id", "default_key"),
    )

    label_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(255))
    default_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

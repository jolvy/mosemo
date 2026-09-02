from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mosemo.accounts.models import AccountProvider


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: UUID
    provider: AccountProvider
    created_at: datetime
    last_authenticated_at: datetime

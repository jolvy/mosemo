from uuid import UUID, uuid7

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.repository import AccountRepository
from mosemo.accounts.service import AccountNotFoundError
from mosemo.devices.repository import DeviceRepository
from mosemo.devices.schemas import DeviceCreateResponse


class DeviceService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DeviceRepository,
        account_repository: AccountRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._account_repository = account_repository

    async def create_device(
        self,
        *,
        account_id: UUID,
        idempotency_key: UUID,
    ) -> DeviceCreateResponse:
        async with self._session.begin():
            account = await self._account_repository.find_by_id(account_id)
            if account is None:
                raise AccountNotFoundError

            existing = await self._repository.find_by_idempotency_key(
                account_id=account_id,
                idempotency_key=idempotency_key,
            )
            candidate_id = uuid7()
            decision = account.register_device(
                idempotency_key=idempotency_key,
                candidate_id=candidate_id,
                existing_registration=existing,
            )
            if decision.should_create:
                inserted = await self._repository.insert(
                    registration=decision.registration,
                )
                if inserted is None:
                    # The unique constraint chooses the winner of concurrent inserts.
                    winner = await self._repository.find_by_idempotency_key(
                        account_id=account_id,
                        idempotency_key=idempotency_key,
                    )
                    if winner is None:
                        raise RuntimeError("device conflict could not be resolved")
                    decision = account.register_device(
                        idempotency_key=idempotency_key,
                        candidate_id=candidate_id,
                        existing_registration=winner,
                    )

        return DeviceCreateResponse(device_id=decision.registration.device_id)

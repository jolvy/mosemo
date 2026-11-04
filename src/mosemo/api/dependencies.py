from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import ActivityService
from mosemo.activity_labels.confirmations.repository import ConfirmationRepository
from mosemo.activity_labels.confirmations.service import ConfirmationService
from mosemo.activity_labels.proposals.repository import ProposalRepository
from mosemo.activity_labels.queries.repository import LabelQueryRepository
from mosemo.activity_labels.queries.service import LabelQueries
from mosemo.activity_labels.service import ActivityLabelService
from mosemo.api.contexts import IDENTITY, LABELS
from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.infra.config import Config
from mosemo.infra.providers import get_clock, get_config, get_session
from mosemo.labels.public import LabelCatalog
from mosemo.shared.application.clock import Clock
from mosemo.shared.presentation.current_account import CurrentAccount

ConfigDep = Annotated[Config, Depends(get_config)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

ClockDep = Annotated[Clock, Depends(get_clock)]


def get_account_directory() -> AccountDirectory:
    return IDENTITY.account_directory


AccountDirectoryDep = Annotated[AccountDirectory, Depends(get_account_directory)]


def get_activity_repository(session: SessionDep) -> ActivityRepository:
    return ActivityRepository(session)


ActivityRepositoryDep = Annotated[
    ActivityRepository,
    Depends(get_activity_repository),
]


def get_device_directory() -> DeviceDirectory:
    return IDENTITY.device_directory


DeviceDirectoryDep = Annotated[DeviceDirectory, Depends(get_device_directory)]


def get_activity_service(
    session: SessionDep,
    repository: ActivityRepositoryDep,
    *,
    devices: DeviceDirectoryDep,
    accounts: AccountDirectoryDep,
    clock: ClockDep,
) -> ActivityService:
    return ActivityService(
        session=session,
        repository=repository,
        devices=devices,
        accounts=accounts,
        clock=clock,
    )


ActivityServiceDep = Annotated[
    ActivityService,
    Depends(get_activity_service),
]


def get_confirmation_repository(session: SessionDep) -> ConfirmationRepository:
    return ConfirmationRepository(session)


ConfirmationRepositoryDep = Annotated[
    ConfirmationRepository,
    Depends(get_confirmation_repository),
]


def get_label_catalog() -> LabelCatalog:
    return LABELS.label_catalog


LabelCatalogDep = Annotated[LabelCatalog, Depends(get_label_catalog)]


def get_proposal_repository(session: SessionDep) -> ProposalRepository:
    return ProposalRepository(session)


ProposalRepositoryDep = Annotated[
    ProposalRepository,
    Depends(get_proposal_repository),
]


def get_label_query_repository(
    session: SessionDep, labels: LabelCatalogDep
) -> LabelQueryRepository:
    return LabelQueryRepository(session, labels=labels)


LabelQueryRepositoryDep = Annotated[
    LabelQueryRepository,
    Depends(get_label_query_repository),
]


def get_confirmation_service(
    session: SessionDep,
    activity_repository: ActivityRepositoryDep,
    *,
    confirmation_repository: ConfirmationRepositoryDep,
    labels: LabelCatalogDep,
) -> ConfirmationService:
    return ConfirmationService(
        session=session,
        activity_repository=activity_repository,
        confirmation_repository=confirmation_repository,
        labels=labels,
    )


ConfirmationServiceDep = Annotated[
    ConfirmationService,
    Depends(get_confirmation_service),
]


def get_label_queries(
    activity_repository: ActivityRepositoryDep,
    confirmation_repository: ConfirmationRepositoryDep,
    *,
    proposal_repository: ProposalRepositoryDep,
    label_query_repository: LabelQueryRepositoryDep,
    accounts: AccountDirectoryDep,
) -> LabelQueries:
    return LabelQueries(
        activity_repository=activity_repository,
        confirmation_repository=confirmation_repository,
        proposal_repository=proposal_repository,
        label_query_repository=label_query_repository,
        accounts=accounts,
    )


LabelQueriesDep = Annotated[LabelQueries, Depends(get_label_queries)]


def get_activity_label_service(
    session: SessionDep,
    activity_repository: ActivityRepositoryDep,
    *,
    confirmations: ConfirmationServiceDep,
    queries: LabelQueriesDep,
    clock: ClockDep,
) -> ActivityLabelService:
    return ActivityLabelService(
        session=session,
        activity_repository=activity_repository,
        confirmations=confirmations,
        queries=queries,
        clock=clock,
    )


ActivityLabelServiceDep = Annotated[
    ActivityLabelService,
    Depends(get_activity_label_service),
]


# Old routers keep verifying the token directly; routes of moved contexts take
# the declared current account provider that the assembly root overrides.
AuthenticatedAccountDep = Annotated[
    CurrentAccount,
    Depends(identity_bootstrap.get_token_current_account),
]

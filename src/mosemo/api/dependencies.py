from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.infra.config import Config
from mosemo.infra.providers import get_clock, get_config, get_session
from mosemo.shared.application.clock import Clock

ConfigDep = Annotated[Config, Depends(get_config)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

ClockDep = Annotated[Clock, Depends(get_clock)]

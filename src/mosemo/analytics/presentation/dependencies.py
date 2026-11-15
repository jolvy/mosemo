from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.platform.providers import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]

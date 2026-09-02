from typing import Literal

from pydantic import BaseModel


class TokenRequest(BaseModel):
    grant_type: Literal["authorization_code"]
    code: str
    code_verifier: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int

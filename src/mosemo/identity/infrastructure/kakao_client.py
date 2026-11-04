from urllib.parse import urlencode

import httpx2

from mosemo.identity.domain.oauth import OAuthClientError

KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USER_INFO_URL = "https://kapi.kakao.com/v2/user/me"


class KakaoClientError(OAuthClientError):
    pass


class KakaoClient:
    def __init__(
        self,
        *,
        http_client: httpx2.AsyncClient,
        rest_api_key: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self._http_client = http_client
        self._rest_api_key = rest_api_key
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def create_authorization_url(self, *, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._rest_api_key,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{KAKAO_AUTHORIZE_URL}?{query}"

    async def get_user_id(self, *, code: str) -> str:
        try:
            token_response = await self._http_client.post(
                KAKAO_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._rest_api_key,
                    "redirect_uri": self._redirect_uri,
                    "code": code,
                    "client_secret": self._client_secret,
                },
            )
            token_response.raise_for_status()

            access_token = token_response.json()["access_token"]

            user_response = await self._http_client.get(
                KAKAO_USER_INFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_response.raise_for_status()
        except (httpx2.HTTPError, KeyError) as exc:
            raise KakaoClientError from exc

        kakao_user = user_response.json()
        return str(kakao_user["id"])

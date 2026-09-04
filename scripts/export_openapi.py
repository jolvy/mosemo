import json
import os
from pathlib import Path

EXPORT_ENVIRONMENT = {
    "MOSEMO_ENV": "openapi",
    "DB_DATABASE": "mosemo_openapi",
    "DB_USER": "mosemo",
    "DB_PASSWORD": "mosemo",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "KAKAO_REST_API_KEY": "openapi-placeholder",
    "KAKAO_CLIENT_SECRET": "openapi-placeholder",
    "KAKAO_REDIRECT_URI": ("http://localhost:8000/api/v1/auth/kakao/callback"),
    "AUTH_JWT_SECRET_KEY": "openapi-placeholder-key-at-least-32-bytes",
    "AUTH_JWT_ISSUER": "mosemo",
    "AUTH_JWT_AUDIENCE": "mosemo-api",
    "AUTH_ACCESS_TOKEN_TTL_SECONDS": "86400",
    "AUTH_AUTHORIZATION_CODE_TTL_SECONDS": "60",
    "AUTH_MACOS_CALLBACK_URI": "com.example.mosemo:/auth/callback",
}

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPOSITORY_ROOT / "openapi" / "openapi.json"


def main() -> None:
    os.environ.update(EXPORT_ENVIRONMENT)

    from mosemo.main import app

    document = json.dumps(
        app.openapi(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(f"{document}\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

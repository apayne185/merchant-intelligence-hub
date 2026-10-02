"""
Mints an HS256 access token for local docker-compose / manual testing of
AUTH_MODE=jwt (DECISIONS.md D15). Production tokens come from the IdP
(AUTH_JWKS_URL), never from this script.

    TOKEN=$(uv run python -m scripts.mint_dev_token)
    curl -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
         -d '{"question": "What does onboarding require?"}' localhost:8001/ask
"""
from __future__ import annotations

import argparse
import os
import sys

from src.copilot.infra.auth import mint_dev_token


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--secret", default=os.environ.get("AUTH_JWT_SECRET"))
    ap.add_argument("--subject", default="dev-user")
    ap.add_argument("--scope", action="append", dest="scopes", help="repeatable; default copilot:ask")
    ap.add_argument("--ttl", type=int, default=3600)
    ap.add_argument("--audience", default=os.environ.get("AUTH_JWT_AUDIENCE"))
    ap.add_argument("--issuer", default=os.environ.get("AUTH_JWT_ISSUER"))
    args = ap.parse_args(argv)
    if not args.secret:
        print("error: pass --secret or set AUTH_JWT_SECRET", file=sys.stderr)
        return 2
    print(
        mint_dev_token(
            args.secret,
            subject=args.subject,
            scopes=tuple(args.scopes or ["copilot:ask"]),
            ttl_seconds=args.ttl,
            audience=args.audience,
            issuer=args.issuer,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

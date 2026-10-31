"""Read-only contract smoke check. Never creates applications or changes the database."""

import asyncio
import json
from pathlib import Path
import sys
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from jobpilot.sources import ADAPTERS


async def main():
    async with httpx.AsyncClient() as client:
        for provider, board, company in [
            ("greenhouse", "jetbrains", "JetBrains"),
            ("greenhouse", "gitlab", "GitLab"),
            ("ashby", "ashby", "Ashby"),
            ("lever", "palantir", "Palantir"),
        ]:
            try:
                jobs = await ADAPTERS[provider](client).fetch(board, company)
                print(
                    json.dumps(
                        {
                            "provider": provider,
                            "board": board,
                            "count": len(jobs),
                            "valid": all(
                                j.canonical_url.startswith("https://") for j in jobs
                            ),
                        }
                    )
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "provider": provider,
                            "error": type(exc).__name__,
                            "detail": str(exc)[:200],
                        }
                    )
                )


if __name__ == "__main__":
    asyncio.run(main())

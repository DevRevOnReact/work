"""Read-only contract smoke check. Never creates applications or changes the database."""

import asyncio
import argparse
import json
from pathlib import Path
import sys
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from jobpilot.sources import ADAPTERS, STARTER_SOURCES
from jobpilot.searches import published_today


async def main(catalog=False, check_companies=False, board_filter=None):
    failures = 0
    if check_companies:
        from jobpilot.companies import discover_employers
        for country in ["United States", "Germany", "Singapore"]:
            try:
                found, errors = await discover_employers(country)
                print(json.dumps({"country": country, "found": len(found), "profile_errors": len(errors)}), flush=True)
            except httpx.HTTPError as exc:
                failures += 1
                print(json.dumps({"country": country, "error": type(exc).__name__}), flush=True)
    boards = [(item["provider"], item["board"], item["company"]) for item in STARTER_SOURCES] if catalog else [
        ("greenhouse", "stripe", "Stripe"), ("greenhouse", "gitlab", "GitLab"),
        ("ashby", "ashby", "Ashby"), ("lever", "palantir", "Palantir"),
        ("remotive", "all", "Remotive"), ("arbeitnow", "all", "Arbeitnow"),
    ]
    if board_filter:
        boards = [(item["provider"], item["board"], item["company"]) for item in STARTER_SOURCES if item["board"] == board_filter]
        if not boards:
            raise ValueError("Board is absent from the catalog")
    report = []
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient() as client:
        async def check(provider, board, company):
            nonlocal failures
            try:
                async with semaphore:
                    jobs = await asyncio.wait_for(ADAPTERS[provider](client).fetch(board, company), timeout=90)
                item = {"provider": provider, "board": board, "count": len(jobs),
                        "published_today": sum(published_today(job) for job in jobs),
                        "unknown_dates": sum(job.published_at is None for job in jobs),
                        "valid": all(j.canonical_url.startswith("https://") for j in jobs)}
                if not item["valid"]:
                    failures += 1
                    item["invalid_urls"] = [job.canonical_url for job in jobs if not job.canonical_url.startswith("https://")]
            except Exception as exc:
                failures += 1
                item = {"provider": provider, "board": board, "error": type(exc).__name__,
                        "status": exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None}
            report.append(item)
            print(json.dumps(item), flush=True)
        await asyncio.gather(*(check(*board) for board in boards))
    if catalog:
        target = Path(__file__).resolve().parents[1] / "data/source-api-audit.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"boards": len(boards), "failures": failures}), flush=True)
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("--companies", action="store_true")
    parser.add_argument("--board")
    args = parser.parse_args()
    raise SystemExit(1 if asyncio.run(main(args.catalog, args.companies, args.board)) else 0)

"""Verify public boards and load 100 employers ranked for this frontend resume."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / 'data/jobpilot-local.db').as_posix()}"
from jobpilot.companies import save_company
from jobpilot.db import SessionLocal
from jobpilot.models import Source, now
from jobpilot.sources import ADAPTERS, STARTER_SOURCES

# Candidates only, never connected until a live feed passes validation.
CANDIDATES = {
    "greenhouse": """
jetbrains:JetBrains automattic:Automattic canonical:Canonical remotecom:Remote remote:Remote
doist:Doist buffer:Buffer hotjar:Hotjar duckduckgo:DuckDuckGo zapier:Zapier
grafanalabs:Grafana_Labs supabase:Supabase netlify:Netlify webflow:Webflow contentful:Contentful
sanity:Sanity algolia:Algolia elastic:Elastic clickhouse:ClickHouse cockroachlabs:Cockroach_Labs
hashicorp:HashiCorp sourcegraph:Sourcegraph sentry:Sentry postman:Postman circleci:CircleCI
launchdarkly:LaunchDarkly planetscale:PlanetScale render:Render digitalocean98:DigitalOcean
digitalocean:DigitalOcean fastly:Fastly tailscale:Tailscale pulumi:Pulumi
temporaltechnologies:Temporal airbyte:Airbyte dbtlabsinc:dbt_Labs dagsterlabs:Dagster_Labs
prefect:Prefect astronomer:Astronomer airtable:Airtable amplitude:Amplitude mixpanel:Mixpanel
heap:Heap fullstory:FullStory pendo:Pendo braze:Braze customerio:Customer.io intercom:Intercom
gorgias:Gorgias gongio:Gong typeform:Typeform calendly:Calendly miro:Miro retool:Retool
appsmith:Appsmith budibase:Budibase clickup:ClickUp smartsheet:Smartsheet hubspotjobs:HubSpot
hubspot:HubSpot klaviyo:Klaviyo chime:Chime plaid:Plaid wise:Wise transferwise:Wise
revolut:Revolut monzo:Monzo starlingbank:Starling_Bank n26:N26 checkoutcom:Checkout.com
adyen:Adyen mollie:Mollie sumup:SumUp rapyd:Rapyd payoneer:Payoneer deel:Deel
oysterhr:Oyster omnipresent:Omnipresent papayaglobal:Papaya_Global
preply:Preply lingoda:Lingoda busuu:Busuu udemy:Udemy coursera:Coursera khanacademy:Khan_Academy
quizlet:Quizlet brilliant:Brilliant masterclass:MasterClass canva:Canva grammarly:Grammarly
lightricks:Lightricks picsart:Picsart runwayml:Runway descript:Descript synthesia:Synthesia
unity3d:Unity epicgames:Epic_Games roblox:Roblox riotgames:Riot_Games scopely:Scopely
playrix:Playrix playkot:Playkot playtika:Playtika moonactive:Moon_Active dreamgames:Dream_Games
tripledotstudios:Tripledot_Studios voodoo:Voodoo bumbleinc:Bumble soundcloud:SoundCloud
vimeo:Vimeo wistia:Wistia loom:Loom snap:Snap quora:Quora wikimedia:Wikimedia_Foundation
mozilla:Mozilla proton:Proton 1password:1Password bitwarden:Bitwarden dashlane:Dashlane
nordsecurity:Nord_Security surfshark:Surfshark crowdstrike:CrowdStrike sentinelone:SentinelOne
snyk:Snyk wizinc:Wiz drata:Drata dataiku:Dataiku databricks:Databricks snowflake:Snowflake
confluent:Confluent newrelic:New_Relic influxdata:InfluxData timescale:Timescale questdb:QuestDB
neon:Neon redis:Redis redislabs:Redis superside:Superside toptal:Toptal andela:Andela
testgorilla:TestGorilla greenhouse:Greenhouse personio:Personio factorial:Factorial
semrush:Semrush ahrefs:Ahrefs similarweb:Similarweb bolt:Bolt wolt:Wolt
deliveryhero:Delivery_Hero glovo:Glovo bookingcom:Booking.com skyscanner:Skyscanner
travelperk:TravelPerk getyourguide:GetYourGuide omio:Omio kiwicom:Kiwi.com
criteo:Criteo contentsquare:Contentsquare smartlyio:Smartly.io optimizely:Optimizely
atlassian:Atlassian boxinc:Box okta:Okta auth0:Auth0 workato:Workato
jobber:Jobber clio:Clio toasttab:Toast lightspeedhq:Lightspeed
""",
    "ashby": """
supabase:Supabase vercel:Vercel remote:Remote oyster:Oyster calcom:Cal.com cal:Cal.com
posthog:PostHog resend:Resend inngest:Inngest triggerdotdev:Trigger.dev railway:Railway
render:Render neon:Neon turso:Turso clerk:Clerk convex:Convex liveblocks:Liveblocks livekit:LiveKit
sanity:Sanity strapi:Strapi payload:Payload builderio:Builder.io plasmic:Plasmic framer:Framer
webflow:Webflow retool:Retool appsmith:Appsmith budibase:Budibase clickhouse:ClickHouse
grafanalabs:Grafana_Labs metabase:Metabase hex:Hex motherduck:MotherDuck baseten:Baseten
replicate:Replicate huggingface:Hugging_Face anysphere:Anysphere codeium:Codeium windsurf:Windsurf
augmentcode:Augment_Code poolside:Poolside cognition:Cognition sourcegraph:Sourcegraph
udio:Udio runway:Runway runwayml:Runway pika:Pika heygen:HeyGen synthesia:Synthesia
descript:Descript captions:Captions granola:Granola gamma:Gamma lovable:Lovable stackblitz:StackBlitz
attio:Attio folk:Folk clay:Clay apollo:Apollo apolloio:Apollo.io lempire:lempire loops:Loops
customerio:Customer.io beehiiv:beehiiv kit:Kit convertkit:Kit substack:Substack ghost:Ghost
buffer:Buffer brex:Brex rippling:Rippling gusto:Gusto remotecom:Remote plane:Plane
pilot:Pilot pulley:Pulley carta:Carta drata:Drata secureframe:Secureframe
conductorone:ConductorOne tailscale:Tailscale teleport:Teleport infisical:Infisical
1password:1Password bitwarden:Bitwarden unleash:Unleash flagsmith:Flagsmith growthbook:GrowthBook
statsig:Statsig eppo:Eppo n8n:n8n windmill:Windmill activepieces:Activepieces kestra:Kestra
temporal:Temporal hatchet:Hatchet prefect:Prefect dagster:Dagster airbyte:Airbyte astronomer:Astronomer
langfuse:Langfuse langchain:LangChain braintrust:Braintrust arize:Arize_AI helicone:Helicone
mintlify:Mintlify fern:Fern speakeasy:Speakeasy readme:ReadMe scalar:Scalar stainless:Stainless
incident:incident.io incidentio:incident.io rootly:Rootly betterstack:Better_Stack axiom:Axiom
signoz:SigNoz highlight:Highlight postman:Postman hoppscotch:Hoppscotch chromatic:Chromatic
browserbase:Browserbase browserstack:BrowserStack lambdatest:LambdaTest checkly:Checkly qase:Qase
maze:Maze dovetail:Dovetail userinterviews:User_Interviews sprig:Sprig survicate:Survicate
hotjar:Hotjar fullstory:FullStory amplitude:Amplitude mixpanel:Mixpanel census:Census
hightouch:Hightouch rudderstack:RudderStack segment:Segment snowplow:Snowplow tinybird:Tinybird
deepgram:Deepgram assemblyai:AssemblyAI cartesia:Cartesia fal:fal together:Together_AI
fireworks:Fireworks_AI groq:Groq cohere:Cohere mistral:Mistral_AI lightdash:Lightdash rows:Rows
""",
    "lever": """
spotify:Spotify canva:Canva miro:Miro figma:Figma duckduckgo:DuckDuckGo automattic:Automattic
buffer:Buffer doist:Doist toggl:Toggl hotjar:Hotjar gitbook:GitBook remote:Remote
lingoda:Lingoda preply:Preply brilliant:Brilliant duolingo:Duolingo quizlet:Quizlet kahoot:Kahoot
busuu:Busuu memrise:Memrise udemy:Udemy coursera:Coursera codecademy:Codecademy sololearn:Sololearn
skillshare:Skillshare pluralsight:Pluralsight datacamp:DataCamp typeform:Typeform personio:Personio
factorial:Factorial pleo:Pleo qonto:Qonto alan:Alan pennylane:Pennylane payfit:PayFit
spendesk:Spendesk agicap:Agicap lempire:lempire aircall:Aircall front:Front gorgias:Gorgias
helpscout:Help_Scout intercom:Intercom zendesk:Zendesk freshworks:Freshworks livestorm:Livestorm
whereby:Whereby pitch:Pitch slite:Slite slack:Slack notion:Notion anytype:Anytype
craft:Craft readwise:Readwise superhuman:Superhuman linear:Linear shortcut:Shortcut
motion:Motion reclaim:Reclaim calendly:Calendly clockwise:Clockwise zapier:Zapier
make:Make trayio:Tray.ai workato:Workato ifttt:IFTTT airtable:Airtable smartsheet:Smartsheet
clickup:ClickUp asana:Asana productboard:Productboard aha:Aha canny:Canny dovetail:Dovetail
maze:Maze usertesting:UserTesting contentsquare:Contentsquare fullstory:FullStory heap:Heap
mixpanel:Mixpanel amplitude:Amplitude segment:Segment rudderstack:RudderStack posthog:PostHog
launchdarkly:LaunchDarkly optimizely:Optimizely abtasty:AB_Tasty unbounce:Unbounce
instapage:Instapage getresponse:GetResponse brevo:Brevo mailerlite:MailerLite
activecampaign:ActiveCampaign customerio:Customer.io klaviyo:Klaviyo omnisend:Omnisend
iterable:Iterable braze:Braze onesignal:OneSignal clevertap:CleverTap moengage:MoEngage
revolut:Revolut wise:Wise monzo:Monzo n26:N26 sumup:SumUp mollie:Mollie adyen:Adyen bolt:Bolt
wolt:Wolt getyourguide:GetYourGuide skyscanner:Skyscanner omio:Omio kiwicom:Kiwi.com
travelperk:TravelPerk hopper:Hopper tripadvisor:Tripadvisor hostaway:Hostaway hostinger:Hostinger
guesty:Guesty lodgify:Lodgify cloudbeds:Cloudbeds mews:Mews apaleo:Apaleo toptal:Toptal andela:Andela
lemonade:Lemonade insurify:Insurify wefox:wefox getsafe:Getsafe hedvig:Hedvig zego:Zego
""",
}


def candidates():
    items = {(s["provider"], s["board"]): s for s in STARTER_SOURCES if s["provider"] in CANDIDATES}
    for provider, entries in CANDIDATES.items():
        for entry in entries.split():
            board, company = entry.split(":", 1)
            items[provider, board] = {"provider": provider, "board": board, "company": company.replace("_", " ")}
    return list(items.values())


async def main():
    semaphore = asyncio.Semaphore(6)
    verified, failures = [], []
    items = candidates()
    checked_at = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async def check(item):
            async with semaphore:
                try:
                    jobs = await asyncio.wait_for(ADAPTERS[item["provider"]](client).fetch(item["board"], item["company"]), 45)
                    relevant = [j for j in jobs if re.search(r"front[ -]?end|full[ -]?stack|web (?:developer|engineer)|javascript|typescript|vue|react|pixi|ui engineer", j.title, re.I)]
                    ranked = []
                    for job in relevant:
                        text = job.description.casefold()
                        skills = [s for s in ("react", "vue", "typescript", "javascript", "next.js", "pixi", "webgl", "storybook", "playwright") if s in text]
                        remote = "remote" in job.location.casefold()
                        worldwide = remote and bool(re.search(r"worldwide|anywhere|global|emea|europe|georgia", job.location, re.I))
                        score = min(100, len(skills) * 9 + 15 * remote + 20 * worldwide + 5 * bool(re.search("senior", job.title, re.I)))
                        ranked.append({"title": job.title, "url": job.canonical_url, "location": job.location, "skills": skills, "score": score})
                    ranked.sort(key=lambda j: -j["score"])
                    verified.append({**item, "checked_at": checked_at, "jobs": len(jobs), "matching_jobs": len(relevant), "score": ranked[0]["score"] if ranked else 0, "examples": ranked[:3]})
                except Exception as exc:
                    failures.append({**item, "error": str(exc)[:180]})
                total = len(verified) + len(failures)
                if total % 25 == 0:
                    print(f"Checked {total}/{len(items)}; accessible boards: {len(verified)}", flush=True)
        await asyncio.gather(*(check(item) for item in items))
    verified.sort(key=lambda item: (-item["score"], -item["matching_jobs"], item["company"].casefold()))
    unique = {}
    for item in verified:
        if item["jobs"]:
            unique.setdefault(item["company"].casefold(), item)
    top = list(unique.values())[:100]
    report = {"checked_at": checked_at, "candidates": len(items), "accessible_boards": len(verified), "unique_employers": len(unique), "top": top, "failures": failures}
    (ROOT / "data/global-source-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(top) < 100:
        raise RuntimeError(f"Only {len(top)} verified employers; report saved, no source changes made")
    catalog = [{k: item[k] for k in ("provider", "board", "company")} for item in top]
    (ROOT / "backend/jobpilot/starter_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    with SessionLocal() as db:
        added = 0
        for rank, item in enumerate(top, 1):
            source = db.scalar(select(Source).where(Source.provider == item["provider"], Source.board == item["board"]))
            if source is None:
                db.add(Source(provider=item["provider"], board=item["board"], company=item["company"], synced_at=now()))
                added += 1
            url = {"greenhouse": "https://boards.greenhouse.io/", "ashby": "https://jobs.ashbyhq.com/", "lever": "https://jobs.lever.co/"}[item["provider"]] + item["board"]
            best = item["examples"][0] if item["examples"] else None
            note = (f"Frontend/full-stack вакансий: {item['matching_jobs']}. "
                    + (f"Пример: {best['title']}; {best['location']}. Стек: {', '.join(best['skills'])}. " if best else "")
                    + "Подборка по Vue/React/TypeScript/PixiJS. Возможность работы из Грузии требует проверки.")
            save_company(db, item["company"], url, {"kind": "resume_fit", "url": best["url"] if best else url,
                         "note": note, "location": best["location"] if best else "", "checked_at": checked_at[:10], "confidence": "public_jobs", "rank": rank})
        db.commit()
    print(f"Connected {added} new sources; top catalog: {len(top)}; with relevant openings: {sum(bool(i['matching_jobs']) for i in top)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

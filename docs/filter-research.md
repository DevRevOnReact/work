# Filter research and integration decisions

Reviewed 31 October 2026 against official product documentation.

| Product | Documented behavior | JobPilot decision |
|---|---|---|
| [LinkedIn classic search](https://www.linkedin.com/help/linkedin/answer/a507441/filter-and-sort-job-search-results?lang=en) | Location, date windows, multiple companies, experience and employment criteria | Put title, locations, companies and date windows near the top. Support alternatives within these text fields. |
| [LinkedIn current AI search](https://www.linkedin.com/help/linkedin/answer/a6889044/) | Classic search retirement started in September; suggested filters sit below search, while several sorting/work-format controls are still returning | Keep deterministic, editable filters. Do not depend on an AI query parser or assume the older interface is still universal. |
| [Indeed](https://www.indeed.com/career-advice/finding-a-job/how-does-indeed-work) | Start with search criteria, then refine results through advanced filters | Separate common controls from advanced criteria. Retain the existing fields rather than dropping less frequent parameters. |
| [Welcome to the Jungle](https://help.welcometothejungle.com/en/filter-your-job-search-on-welcome-to-the-jungle) | Multiple places and titles, explicit work-format preferences, contract, experience and pay preferences | Group related choices and allow multiple alternatives. Keep profile preferences separate from strict job filters. |
| [Welcome to the Jungle: broadening results](https://help.welcometothejungle.com/en/if-im-getting-fewer-results-how-can-i-make-sure-im-not-missing-anything-how-can-i-broaden-my-selection) | Title/location/work-format criteria can over-restrict matches | Default resume skills to any-of and show removable active criteria plus an empty-state explanation. |

These UI choices are an adaptation to the current employer-feed data, not an assertion that JobPilot has the same indexes or ranking systems as these products. Controls such as company size, applicant count and normalized salary ranges require data absent from the current feeds; they are not shown as nonfunctional filters.

## Matching contract

Across fields use AND. Within comma-separated title/company/location values use OR. Skills are comma-separated whole terms with explicit any/all mode. Keywords are space-separated with explicit any/all mode. Excluded comma-separated phrases remove a posting if any matches. Existing scalar filters and exact numeric values are preserved when editing older searches. Blank/whitespace fields impose no restriction.

Workplace format prioritizes the stated location. Hybrid/onsite signals take precedence over a generic remote mention in mixed locations. If no location format exists, use only explicit role phrases in the description; an unknown format stays unknown. Date filters omit unknown dates. Pay and employment fields remain literal text searches; exchange rates, pay-period conversion and structured employment classification are outside the current data contract.

Simple filters are evaluated before resume scoring. Scored results apply eligibility and minimum score, deduplicate canonical URLs and sort on the server before the 500-result storage bound. UI pagination renders 20 results at a time. Draft changes are applied by saving and rerunning the saved search, so the displayed results always carry a persisted filter configuration. Errors and incomplete source runs are shown beside the results.

## Company evidence

- [GitLab referral program](https://handbook.gitlab.com/handbook/hiring/referral-process/): official program with eligibility rules.
- [Wise Hyderabad](https://wise.jobs/hyderabad-office): employee referral scheme scoped to that office page.
- [Atlassian US Early Career](https://join.atlassian.com/usreferralearlycareer/talentcommunity/form): scoped referral form, not a universal program claim.
- [JetBrains careers](https://www.jetbrains.com/careers/): Armenia office signal, kept distinct from self-reported employee location.
- [GitHub public search API](https://docs.github.com/en/rest/search/search): bounded public-user search by location followed by public company/location fields. Store source links and dates; surface rate limits and incomplete results. No inferred citizenship or guaranteed referral claims.

import json

import pytest

from jobpilot.security import is_submission_request


@pytest.mark.parametrize("provider,url,body", [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/example/jobs/123", None),
    ("greenhouse", "https://job-boards.greenhouse.io/example/jobs/123", None),
    ("lever", "https://jobs.lever.co/example/123/apply", None),
    ("lever", "https://api.lever.co/v0/postings/example/123", None),
    ("ashby", "https://api.ashbyhq.com/applicationForm.submit", None),
    ("ashby", "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSubmitApplicationForm", None),
    ("ashby", "https://jobs.ashbyhq.com/api/non-user-graphql",
     json.dumps({"operationName": "ApiSubmitApplicationForm", "variables": {}})),
    ("ashby", "https://jobs.ashbyhq.com/api/non-user-graphql",
     json.dumps({"query": "mutation X { applicationFormSubmit(input: $input) { success } }"})),
    ("workable", "https://apply.workable.com/api/v1/accounts/example/jobs/ABC/applications", None),
    ("smartrecruiters", "https://api.smartrecruiters.com/postings/123/candidates", None),
    ("generic", "https://careers.example.org/applications", None),
    ("generic", "https://careers.example.org/submit", None),
    ("generic", "https://careers.example.org/%73ubmit", None),
])
def test_final_submission_is_blockable_before_intent(provider, url, body):
    assert is_submission_request(provider, url, "POST", body)
    assert not is_submission_request(provider, url, "GET", body)


@pytest.mark.parametrize("provider,url,body", [
    ("ashby", "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", None),
    ("ashby", "https://jobs.ashbyhq.com/api/non-user-graphql",
     json.dumps({"operationName": "ApiApplicationForm", "variables": {"question": "submitApplication"}})),
    ("ashby", "https://jobs.ashbyhq.com/api/non-user-graphql",
     json.dumps({"query": "query ApiApplicationForm { applicationForm { fields } }"})),
    ("greenhouse", "https://job-boards.greenhouse.io/example/jobs/123/uploads", None),
    ("lever", "https://jobs.lever.co/example/123/resume", None),
    ("lever", "https://jobs.lever.co/example/123/apply/resume", None),
    ("workable", "https://apply.workable.com/api/v1/applications/123/attachments", None),
    ("smartrecruiters", "https://api.smartrecruiters.com/candidates/123/files", None),
    ("generic", "https://careers.example.org/parse-resume", "not-json"),
    ("generic", "https://careers.example.org/api/form", "{malformed"),
])
def test_reads_and_document_uploads_do_not_look_like_final_submission(provider, url, body):
    assert not is_submission_request(provider, url, "POST", body)

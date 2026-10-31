import httpx
from jobpilot import workers


def test_telegram_url_secret_is_not_logged(monkeypatch, capsys):
    secret = "must-not-appear-in-log"

    def fail():
        request = httpx.Request("GET", f"https://api.telegram.org/bot{secret}/getUpdates")
        raise httpx.HTTPStatusError(
            f"Failed request {request.url}", request=request, response=httpx.Response(401)
        )

    monkeypatch.setattr("jobpilot.telegram.poll_commands", fail)
    workers.telegram_commands()
    output = capsys.readouterr().out
    assert secret not in output
    assert "HTTPStatusError" in output

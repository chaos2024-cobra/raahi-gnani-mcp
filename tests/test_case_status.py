import httpx

from src.errors import ErrorCategory
from src.gnani_tools import pull_case_status_into_call
from tests.fixtures import FakeSender, case_payload, json_response, timeout_error


async def test_case_status_local_success_by_case_id(sender):
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    assert result.success is True
    assert result.error_category is ErrorCategory.SUCCESS
    assert result.case_id == "RAAHI-DEMO-001"
    assert result.applicant_id == "APPLICANT-DEMO-001"
    states = {item.item: item.status for item in result.checklist}
    assert states == {
        "passport": "verified",
        "funds_proof": "pending",
        "bank_letter": "pending",
        "insurance": "verified",
    }
    assert result.source == "local"
    assert sender.requests == []


async def test_case_status_local_success_by_applicant_id(sender):
    result = await pull_case_status_into_call(applicant_id="APPLICANT-DEMO-001")
    assert result.success is True
    assert result.case_id == "RAAHI-DEMO-001"


async def test_case_status_not_found(sender):
    result = await pull_case_status_into_call(case_id="RAAHI-DOES-NOT-EXIST")
    assert result.success is False
    assert result.error_category is ErrorCategory.NOT_FOUND
    assert result.checklist == []


async def test_case_status_validation_without_ids(sender):
    result = await pull_case_status_into_call()
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert "case_id or applicant_id is required" in result.error.message


async def test_case_status_http_success(sender, monkeypatch):
    monkeypatch.setenv("GNANI_CASE_STATUS_URL", "https://backend.example.test/cases/{case_id}/checklist")
    sender.push(json_response(case_payload()))
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    assert result.success is True
    assert result.source == "http"
    states = {item.item: item.status for item in result.checklist}
    assert states["passport"] == "verified"
    request = sender.requests[0]
    assert request.url == "https://backend.example.test/cases/RAAHI-DEMO-001/checklist"
    assert request.timeout == 3.0


async def test_case_status_http_timeout_returns_immediately(sender, monkeypatch):
    monkeypatch.setenv("GNANI_CASE_STATUS_URL", "https://backend.example.test/cases")
    monkeypatch.setenv("GNANI_CASE_STATUS_TIMEOUT_SECONDS", "2")
    sender.set_default(timeout_error())
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    assert result.success is False
    assert result.error_category is ErrorCategory.TIMEOUT
    assert result.error.retryable is True
    assert "bounded timeout" in result.note
    assert sender.requests[0].timeout == 2.0
    assert len(sender.requests) == 1


async def test_case_status_http_missing(sender, monkeypatch):
    monkeypatch.setenv("GNANI_CASE_STATUS_URL", "https://backend.example.test/cases")
    sender.push(json_response({"error": "nope"}, status=404))
    result = await pull_case_status_into_call(case_id="RAAHI-NOPE")
    assert result.error_category is ErrorCategory.NOT_FOUND


async def test_case_status_http_malformed_response(sender, monkeypatch):
    monkeypatch.setenv("GNANI_CASE_STATUS_URL", "https://backend.example.test/cases")
    sender.push(json_response({"unexpected": True}))
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    assert result.error_category is ErrorCategory.MALFORMED_RESPONSE


async def test_case_status_http_invalid_json(sender, monkeypatch):
    from tests.fixtures import raw_response

    monkeypatch.setenv("GNANI_CASE_STATUS_URL", "https://backend.example.test/cases")
    sender.push(raw_response(b"<html>not json</html>", content_type="text/html"))
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    assert result.error_category is ErrorCategory.MALFORMED_RESPONSE


async def test_case_status_validation_bad_identifier(sender):
    result = await pull_case_status_into_call(case_id="bad id !!")
    assert result.error_category is ErrorCategory.VALIDATION_ERROR
    assert sender.requests == []


async def test_case_status_http_error_status(sender, monkeypatch):
    monkeypatch.setenv("GNANI_CASE_STATUS_URL", "https://backend.example.test/cases")
    sender.push(json_response({"message": "down"}, status=500))
    result = await pull_case_status_into_call(case_id="RAAHI-DEMO-001")
    assert result.error_category is ErrorCategory.PROVIDER_ERROR
    assert result.error.http_status == 500


def _asgi_client():
    import server

    transport = httpx.ASGITransport(app=server.app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_case_status_route_success():
    async with _asgi_client() as client:
        response = await client.get("/case-status/RAAHI-DEMO-001")
    assert response.status_code == 200
    payload = response.json()
    assert payload["case_id"] == "RAAHI-DEMO-001"
    assert len(payload["checklist"]) == 4


async def test_case_status_route_by_query():
    async with _asgi_client() as client:
        response = await client.get("/case-status", params={"applicant_id": "APPLICANT-DEMO-002"})
    assert response.status_code == 200
    assert response.json()["case_id"] == "RAAHI-DEMO-002"


async def test_case_status_route_not_found():
    async with _asgi_client() as client:
        response = await client.get("/case-status/NOPE")
    assert response.status_code == 404
    assert response.json()["error"] == "NOT_FOUND"


async def test_case_status_route_requires_identifier():
    async with _asgi_client() as client:
        response = await client.get("/case-status")
    assert response.status_code == 400
    assert response.json()["error"] == "VALIDATION_ERROR"


async def test_case_status_route_token_auth(monkeypatch):
    monkeypatch.setenv("GNANI_CASE_STATUS_TOKEN", "internal-token")
    async with _asgi_client() as client:
        unauthorized = await client.get("/case-status/RAAHI-DEMO-001")
        authorized = await client.get(
            "/case-status/RAAHI-DEMO-001",
            headers={"Authorization": "Bearer internal-token"},
        )
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200

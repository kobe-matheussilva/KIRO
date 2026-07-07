from kiro.infrastructure.jira_client import JiraClient


def _client() -> JiraClient:
    return JiraClient(
        base_url="https://example.atlassian.net",
        user_email="user@example.com",
        api_token="token",
        customer_profile_field="customfield_12180",
    )


def test_extract_workspace_object_ids_from_list_dict():
    raw = [{"workspaceId": "ws-1", "objectId": "596"}]
    assert JiraClient._extract_workspace_object_ids(raw) == ("ws-1", "596")


def test_extract_workspace_object_ids_from_global_id():
    raw = {"id": "ws-2:777"}
    assert JiraClient._extract_workspace_object_ids(raw) == ("ws-2", "777")


def test_to_ticket_resolves_customer_name_from_assets_label(monkeypatch):
    client = _client()

    monkeypatch.setattr(client, "_fetch_assets_object_label", lambda ws, obj: "My Place")

    issue = {
        "key": "SUP-1",
        "fields": {
            "summary": "Erro no checkout",
            "description": None,
            "labels": [],
            "components": [],
            "status": {"name": "Done"},
            "reporter": {"displayName": "Agente"},
            "customfield_12180": [
                {
                    "workspaceId": "ws-1",
                    "objectId": "596",
                }
            ],
        },
    }

    ticket = client._to_ticket(issue)
    assert ticket.customer_name == "My Place"


def test_to_ticket_prefers_direct_name_when_available():
    client = _client()

    issue = {
        "key": "SUP-1",
        "fields": {
            "summary": "Erro no checkout",
            "description": None,
            "labels": [],
            "components": [],
            "status": {"name": "Done"},
            "reporter": {"displayName": "Agente"},
            "customfield_12180": [{"name": "Cliente XPTO"}],
        },
    }

    ticket = client._to_ticket(issue)
    assert ticket.customer_name == "Cliente XPTO"

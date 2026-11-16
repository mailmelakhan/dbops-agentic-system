from dbops_frontend.api.base import ServiceClient


class AgentApi(ServiceClient):
    service = "agent-api"

    def list_conversations(self) -> list[dict]:
        return self._get("/conversations")

    def create_conversation(self) -> str:
        return self._post("/conversations")["id"]

    def get_messages(self, conv_id: str) -> list[dict]:
        return self._get(f"/conversations/{conv_id}/messages")

    def start_run(self, conv_id: str, message: str) -> str:
        return self._post(f"/conversations/{conv_id}/runs", {"message": message})["run_id"]

    def get_events(self, run_id: str, after_seq: int) -> dict:
        return self._get(f"/runs/{run_id}/events", {"after_seq": after_seq})

    def cancel_run(self, run_id: str) -> None:
        self._post(f"/runs/{run_id}/cancel")

    def running_runs(self, conv_id: str) -> list[dict]:
        return self._get("/runs", {"conversation_id": conv_id, "status": "running"})

    def list_change_requests(self, statuses: list[str] | None = None, database: str | None = None) -> list[dict]:
        return self._get("/change-requests", {"status": ",".join(statuses) if statuses else None,
                                              "database": database})

    def get_change_request(self, cr_id: str) -> dict:
        return self._get(f"/change-requests/{cr_id}")

    def propose_rollback(self, cr_id: str) -> str:
        return self._post(f"/change-requests/{cr_id}/rollback")["change_request_id"]

    def list_audit(self, filters: dict, cursor: str | None = None, limit: int = 50) -> dict:
        return self._get("/audit", {**filters, "cursor": cursor, "limit": limit})

    def audit_facets(self) -> dict:
        return self._get("/audit/facets")

    def list_reports(self) -> list[dict]:
        return self._get("/reports")

    def get_report(self, report_id: str) -> dict:
        return self._get(f"/reports/{report_id}")

    def download_report(self, report_id: str, fmt: str) -> bytes:
        return self._get_bytes(f"/reports/{report_id}/download", {"format": fmt})

    def start_change_history_report(self, filters: dict) -> str:
        body = {k: v for k, v in filters.items() if v is not None}
        return self._post("/reports/change-history", body)["report_id"]

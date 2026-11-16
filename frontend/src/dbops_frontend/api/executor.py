from dbops_frontend.api.base import ServiceClient


class ExecutorApi(ServiceClient):
    """The only client allowed to change anything. Used by the Approvals page alone."""

    service = "executor"

    def approve(self, cr_id: str, mode: str, override_reason: str | None = None) -> dict:
        return self._post(f"/change-requests/{cr_id}/approve", {"mode": mode, "override_reason": override_reason})

    def reject(self, cr_id: str, reason: str) -> dict:
        return self._post(f"/change-requests/{cr_id}/reject", {"reason": reason})

    def withdraw(self, cr_id: str) -> dict:
        return self._post(f"/change-requests/{cr_id}/withdraw")

    def cancel(self, cr_id: str, reason: str) -> dict:
        return self._post(f"/change-requests/{cr_id}/cancel", {"reason": reason})

    def retry_cleanup(self, cr_id: str) -> dict:
        return self._post(f"/change-requests/{cr_id}/retry-cleanup")

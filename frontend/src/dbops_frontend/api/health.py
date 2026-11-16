from dbops_frontend.api.base import ServiceClient


class HealthApi(ServiceClient):
    service = "health-analytics"

    def fleet(self, env: str | None = None, engine: str | None = None) -> dict:
        return self._get("/fleet", {"env": env, "engine": engine})

    def summary(self) -> dict:
        return self._get("/fleet/summary")

    def alarms(self, identifier: str) -> list[dict]:
        return self._get(f"/fleet/{identifier}/alarms")

    def refresh(self) -> None:
        self._post("/refresh")

    def start_fleet_report(self, env: str | None = None) -> str:
        return self._post("/reports/fleet-health", {"env": env})["report_id"]

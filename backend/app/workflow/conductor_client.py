"""Thin Conductor OSS client — wraps the REST API."""

from __future__ import annotations

import httpx

from app.config.settings import settings


class ConductorClient:
    def __init__(self) -> None:
        self.base_url = settings.conductor_server_url.rstrip("/")
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.conductor_auth_mode == "token" and settings.conductor_auth_token:
            self._headers["X-Authorization"] = settings.conductor_auth_token

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers, timeout=30
        )

    # -- Task definitions ----------------------------------------------------

    async def register_task_definitions(self, task_defs: list[dict]) -> None:
        async with self._client() as c:
            r = await c.post("/api/metadata/taskdefs", json=task_defs)
            r.raise_for_status()

    async def get_task_definitions(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/api/metadata/taskdefs")
            r.raise_for_status()
            return r.json()

    # -- Workflow definitions ------------------------------------------------

    async def register_workflow_definition(self, workflow_def: dict) -> None:
        async with self._client() as c:
            r = await c.put("/api/metadata/workflow", json=[workflow_def])
            r.raise_for_status()

    async def get_workflow_definition(
        self, name: str, version: int = 1
    ) -> dict:
        async with self._client() as c:
            r = await c.get(f"/api/metadata/workflow/{name}", params={"version": version})
            r.raise_for_status()
            return r.json()

    # -- Workflow execution --------------------------------------------------

    async def start_workflow(
        self,
        name: str,
        version: int,
        correlation_id: str,
        input_data: dict,
    ) -> str:
        async with self._client() as c:
            r = await c.post(
                "/api/workflow",
                json={
                    "name": name,
                    "version": version,
                    "correlationId": correlation_id,
                    "input": input_data,
                },
            )
            r.raise_for_status()
            return r.text.strip().strip('"')

    async def get_workflow(
        self, workflow_id: str, include_tasks: bool = True
    ) -> dict:
        async with self._client() as c:
            r = await c.get(
                f"/api/workflow/{workflow_id}",
                params={"includeTasks": str(include_tasks).lower()},
            )
            r.raise_for_status()
            return r.json()

    async def get_running_workflows(self, name: str) -> list[dict]:
        async with self._client() as c:
            r = await c.get(f"/api/workflow/running/{name}")
            r.raise_for_status()
            return r.json()

    async def search_workflows(
        self, query: str, start: int = 0, size: int = 20
    ) -> dict:
        async with self._client() as c:
            r = await c.get(
                "/api/workflow/search",
                params={"query": query, "start": start, "size": size},
            )
            r.raise_for_status()
            return r.json()

    async def terminate_workflow(
        self, workflow_id: str, reason: str
    ) -> None:
        async with self._client() as c:
            r = await c.delete(
                f"/api/workflow/{workflow_id}",
                params={"reason": reason},
            )
            r.raise_for_status()

    async def retry_workflow(self, workflow_id: str) -> None:
        async with self._client() as c:
            r = await c.post(f"/api/workflow/{workflow_id}/retry")
            r.raise_for_status()

    # -- Task execution ------------------------------------------------------

    async def get_task(self, task_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/api/tasks/{task_id}")
            r.raise_for_status()
            return r.json()

    async def search_tasks(
        self, query: str, start: int = 0, size: int = 20
    ) -> dict:
        async with self._client() as c:
            r = await c.get(
                "/api/tasks/search",
                params={"query": query, "start": start, "size": size},
            )
            r.raise_for_status()
            return r.json()

    # -- Health --------------------------------------------------------------

    async def health(self) -> dict:
        async with self._client() as c:
            r = await c.get("/health")
            r.raise_for_status()
            return r.json()


conductor = ConductorClient()

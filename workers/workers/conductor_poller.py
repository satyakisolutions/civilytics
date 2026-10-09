"""Generic Conductor OSS task poller."""
import asyncio
import httpx
import structlog
from datetime import datetime, timezone

log = structlog.get_logger()


class TaskPoller:
    def __init__(
        self,
        conductor_url: str,
        headers: dict,
        task_type: str,
        handler,
        worker_id: str,
        poll_interval: float = 1.0,
        batch_size: int = 1,
    ):
        self.conductor_url = conductor_url.rstrip("/")
        self.headers = headers
        self.task_type = task_type
        self.handler = handler
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._running = False

    async def start(self):
        self._running = True
        log.info("poller.start", task_type=self.task_type, worker_id=self.worker_id)
        async with httpx.AsyncClient(
            base_url=self.conductor_url, headers=self.headers, timeout=30
        ) as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"/tasks/poll/batch/{self.task_type}",
                        params={"workerid": self.worker_id, "count": self.batch_size},
                    )
                    if resp.status_code == 200 and resp.json():
                        for task in resp.json():
                            await self._execute(client, task)
                except httpx.HTTPError as e:
                    log.error(
                        "poller.poll_error", task_type=self.task_type, error=str(e)
                    )
                await asyncio.sleep(self.poll_interval)

    async def _execute(self, client: httpx.AsyncClient, task: dict):
        task_id = task.get("taskId", "unknown")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        log.info(
            "task.executing",
            task_type=self.task_type,
            task_id=task_id,
            workflow_id=task.get("workflowInstanceId"),
        )
        try:
            output = await self.handler(task.get("inputData", {}), task)
            await client.post(
                f"/tasks/{task_id}",
                json={
                    "taskId": task_id,
                    "workflowInstanceId": task.get("workflowInstanceId"),
                    "status": "COMPLETED",
                    "outputData": output or {},
                    "workerId": self.worker_id,
                    "logs": [
                        {
                            "log": f"Completed by {self.worker_id}",
                            "createdTime": now_ms,
                        }
                    ],
                },
            )
            log.info("task.completed", task_type=self.task_type, task_id=task_id)
        except Exception as e:
            error_msg = str(e)
            status = (
                "FAILED_WITH_TERMINAL_ERROR"
                if "terminal:" in error_msg
                else "FAILED"
            )
            await client.post(
                f"/tasks/{task_id}",
                json={
                    "taskId": task_id,
                    "workflowInstanceId": task.get("workflowInstanceId"),
                    "status": status,
                    "reasonForIncompletion": error_msg[:500],
                    "workerId": self.worker_id,
                    "logs": [
                        {"log": f"Failed: {error_msg[:200]}", "createdTime": now_ms}
                    ],
                },
            )
            log.error(
                "task.failed",
                task_type=self.task_type,
                task_id=task_id,
                error=error_msg,
            )

    def stop(self):
        self._running = False

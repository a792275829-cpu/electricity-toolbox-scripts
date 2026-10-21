"""Keep the original update widgets while running network work in the service."""
import os
import time
from pathlib import Path
from github_update import Release, Cancelled, UpdateError


class RemoteUpdater:
    def __init__(self, client):
        self.client = client
        self.current = os.environ.get("TOOLBOX_APP_VERSION", "0.1.8")

    def check(self):
        self.client.call("updates.check")
        while True:
            status = self.client.call("updates.status")
            if not status["checking"]:
                if status["error"]:
                    raise UpdateError(status["error"])
                return Release(**status["release"]) if status["release"] else None
            time.sleep(0.2)

    def download(self, release, cancel, progress):
        task_id = self.client.call("updates.download", dict(version=release.version))
        cursor = 0
        while True:
            if cancel.is_set():
                self.client.call("cancel", dict(task_id=task_id))
            batch = self.client.call("events", dict(after=cursor))
            cursor = batch["cursor"]
            for event in batch["events"]:
                if event["task_id"] == task_id and event["kind"] == "progress":
                    progress(event["progress"])
            task = next(t for t in self.client.call("tasks") if t["id"] == task_id)
            if task["state"] == "succeeded":
                return Path(task["result"]["outputs"][0])
            if task["state"] == "cancelled":
                raise Cancelled("已取消下载")
            if task["state"] == "failed":
                raise UpdateError(task["error"])
            time.sleep(0.15)

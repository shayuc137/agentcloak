"""Bounded, page-pinned CDP screencasts with explicit artifact finalization."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import shutil
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from zipfile import ZIP_STORED, ZipFile

from agentcloak.core.errors import BackendError


class ScreenRecording:
    def __init__(
        self,
        page: Any,
        tab_id: int,
        *,
        format: Literal["webm", "zip"],
        max_frames: int,
        max_seconds: int,
    ) -> None:
        self.page = page
        self.tab_id = tab_id
        self.format = format
        self.max_frames = max_frames
        self.max_seconds = max_seconds
        self.frames: list[tuple[bytes, dict[str, Any]]] = []
        self.size = 0
        self.started = time.monotonic()
        self.duration = 0.0
        self.reason = ""
        self.active = False
        self.session: Any = None
        self.timer: asyncio.Task[None] | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        if self.format == "webm" and shutil.which("ffmpeg") is None:
            raise BackendError(
                error="record_encoder_missing",
                hint="WebM recording requires ffmpeg",
                action="install ffmpeg or use record start --format zip",
            )
        self.session = await self.page.context.new_cdp_session(self.page)
        self.session.on("Page.screencastFrame", self._frame)
        self.page.on("close", self._page_closed)
        self.active = True
        try:
            await self.session.send("Page.enable")
            await self.session.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": 80,
                    "maxWidth": 1920,
                    "maxHeight": 1080,
                    "everyNthFrame": 1,
                },
            )
        except BaseException:
            await self.discard()
            raise
        self.timer = asyncio.create_task(self._expire())

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _page_closed(self) -> None:
        self._spawn(self.finish("page_closed"))

    def _frame(self, event: dict[str, Any]) -> None:
        if self.active:
            raw = base64.b64decode(event["data"])
            if (
                len(self.frames) >= self.max_frames
                or self.size + len(raw) > 64 * 1024 * 1024
            ):
                self._spawn(self.finish("limit"))
            else:
                self.frames.append(
                    (
                        raw,
                        {
                            "elapsed": time.monotonic() - self.started,
                            "url": self.page.url,
                            "metadata": event.get("metadata", {}),
                        },
                    )
                )
                self.size += len(raw)
                if len(self.frames) >= self.max_frames:
                    self._spawn(self.finish("limit"))
        self._spawn(self._ack(event["sessionId"]))

    async def _ack(self, identifier: int) -> None:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(1):
                await self.session.send(
                    "Page.screencastFrameAck", {"sessionId": identifier}
                )

    async def _expire(self) -> None:
        await asyncio.sleep(self.max_seconds)
        await self.finish("duration_limit")

    async def finish(self, reason: str = "stopped") -> None:
        async with self.lock:
            if not self.active:
                return
            self.active = False
            self.reason = reason
            self.duration = time.monotonic() - self.started
            if self.timer is not None and self.timer is not asyncio.current_task():
                self.timer.cancel()
            self.page.remove_listener("close", self._page_closed)
            if self.session is not None:
                self.session.remove_listener("Page.screencastFrame", self._frame)
                try:
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(0.2):
                            await self.session.send("Page.stopScreencast")
                finally:
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(0.2):
                            await self.session.detach()

    def status(self) -> dict[str, Any]:
        return {
            "recording": self.active,
            "format": self.format,
            "max_frames": self.max_frames,
            "max_seconds": self.max_seconds,
            "max_bytes": 64 * 1024 * 1024,
            "tab_id": self.tab_id,
            "frames": len(self.frames),
            "size": self.size,
            "reason": self.reason,
            "duration_ms": round(
                (time.monotonic() - self.started if self.active else self.duration)
                * 1000
            ),
        }

    async def discard(self) -> None:
        await self.finish("session_closed")
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.timer is not None:
            self.timer.cancel()
            await asyncio.gather(self.timer, return_exceptions=True)
        self.frames.clear()

    async def export(self) -> dict[str, Any]:
        await self.finish()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if not self.frames:
            raise BackendError(
                error="record_empty",
                hint="No screencast frames were received",
                action="start recording on a visible page and wait for a paint",
            )
        manifest = {
            **self.status(),
            "frames": [
                {"file": f"{i:06d}.jpg", **metadata}
                for i, (_, metadata) in enumerate(self.frames)
            ],
        }
        if self.format == "zip":
            stream = BytesIO()
            with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                for i, (raw, _) in enumerate(self.frames):
                    archive.writestr(f"{i:06d}.jpg", raw)
            data = stream.getvalue()
        else:
            data = await self._webm()
        return {
            **self.status(),
            "base64": base64.b64encode(data).decode(),
            "size": len(data),
        }

    async def _webm(self) -> bytes:
        from PIL import Image

        with Image.open(BytesIO(self.frames[0][0])) as image:
            width, height = image.size
        with tempfile.TemporaryDirectory(prefix="agentcloak-record-") as directory:
            root = Path(directory)
            lines: list[str] = []
            for i, (raw, metadata) in enumerate(self.frames):
                name = f"{i:06d}.jpg"
                (root / name).write_bytes(raw)
                end = (
                    self.frames[i + 1][1]["elapsed"]
                    if i + 1 < len(self.frames)
                    else self.duration
                )
                start = metadata["elapsed"] if i else 0
                lines.extend(
                    [f"file '{name}'", f"duration {max(0.001, end - start):.6f}"]
                )
            lines.append(f"file '{len(self.frames) - 1:06d}.jpg'")
            (root / "frames.txt").write_text("\n".join(lines) + "\n")
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                "frames.txt",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                "-c:v",
                "libvpx-vp9",
                "-deadline",
                "realtime",
                "-cpu-used",
                "8",
                "-pix_fmt",
                "yuv420p",
                "-y",
                "recording.webm",
                cwd=root,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await process.communicate()
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise BackendError(
                    error="record_encode_failed",
                    hint=stderr.decode(errors="replace")[-500:],
                    action="use ZIP output or check the ffmpeg encoder",
                )
            return (root / "recording.webm").read_bytes()

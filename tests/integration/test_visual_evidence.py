"""Decode actual recordings and verify annotation geometry in screenshot pixels."""

import asyncio
import base64
import contextlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from PIL import Image

from .test_recovery_evidence import evaluate, navigate
from .test_recovery_evidence import private_api as private_api


async def test_annotation_pixels_and_refs(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, page="visual-evidence.html")
    original = (await evaluate(client, "[innerWidth,innerHeight,devicePixelRatio]"))[
        "result"
    ]
    for full in (False, True):
        if full:
            await evaluate(client, "scrollTo(0, 500)")
        response = await client.get(
            "/screenshot",
            params={
                "format": "png",
                "annotate": True,
                "viewport": "640x480",
                "dpr": 2,
                "full_page": full,
            },
        )
        assert response.is_success, response.text
        data = response.json()["data"]
        assert data["annotated"]
        name = "Lower control" if full else "Save example"
        box = next(item for item in data["annotations"] if item["name"] == name)
        assert box["box"][:2] == [100, 900 if full else 80]
        with Image.open(BytesIO(base64.b64decode(data["base64"]))) as picture:
            assert picture.size == (data["pixel_width"], data["pixel_height"])
            x, y, _, height = box["box"]
            assert picture.convert("RGB").getpixel(
                (round((x + 5) * 2), round((y + height - 1) * 2))
            ) == (224, 32, 32)
        assert (await evaluate(client, "[innerWidth,innerHeight,devicePixelRatio]"))[
            "result"
        ] == original
        clicked = await client.post(
            "/action", json={"kind": "click", "index": box["ref"]}
        )
        assert clicked.is_success, clicked.text
    assert (
        await evaluate(
            client, "document.querySelectorAll('[data-cloak-annotation]').length"
        )
    )["result"] == 0


async def wait_frames(client, minimum=2):
    async with asyncio.timeout(5):
        while True:
            response = await client.get("/record/status")
            assert response.is_success, response.text
            if response.json()["data"]["frames"] >= minimum:
                return response.json()["data"]
            await asyncio.sleep(0.03)


async def test_annotation_filters_share_snapshot_refs(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, page="visual-evidence.html")
    for params in (
        {"within": "#control"},
        {"find": "Save example"},
        {"within": "body", "find": "Save example", "limit": 0},
    ):
        response = await client.get("/screenshot", params={"annotate": True, **params})
        assert response.is_success, response.text
        annotations = response.json()["data"]["annotations"]
        assert any(item["name"] == "Save example" for item in annotations)
        assert not any(item["name"] == "Lower control" for item in annotations)
    limited = await client.get("/screenshot", params={"annotate": True, "limit": 1})
    assert limited.is_success, limited.text
    assert len(limited.json()["data"]["annotations"]) <= 1
    missing = await client.get(
        "/screenshot", params={"annotate": True, "find": "missing-control"}
    )
    assert missing.json()["data"]["annotations"] == []
    for params in (
        {"within": "body"},
        {"limit": 0},
        {"find": "Save"},
        {"annotate": True, "limit": -1},
    ):
        response = await client.get("/screenshot", params=params)
        assert response.is_error, response.text


@pytest.mark.parametrize("format", ["webm", "zip"])
async def test_recording_export_and_session_ownership(
    private_api, local_server, tmp_path, format
):
    client, _ = private_api
    await navigate(client, local_server, page="visual-evidence.html")
    await client.post("/viewport", json={"width": 320, "height": 200})
    response = await client.post("/record/start", json={"format": format})
    assert response.is_success, response.text
    pinned = response.json()["data"]["tab_id"]
    duplicate = await client.post("/record/start", json={"format": format})
    assert duplicate.json()["error"] == "record_already_started"
    other = {"X-Agentcloak-Session": "recording-sibling"}
    assert (await client.get("/record/status", headers=other)).json()["data"][
        "frames"
    ] == 0
    for color in ("red", "blue", "green"):
        await evaluate(client, f"document.body.style.background='{color}'")
        await asyncio.sleep(0.08)
    await wait_frames(client)
    await client.post("/tab/new", json={"url": f"{local_server}/index.html"})
    stopped = await client.post("/record/stop")
    assert stopped.is_success, stopped.text
    data = stopped.json()["data"]
    assert not data["recording"] and data["frames"] >= 2 and data["tab_id"] == pinned
    raw = base64.b64decode(data["base64"])
    assert len(raw) == data["size"]
    if format == "zip":
        with ZipFile(BytesIO(raw)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert all(
                frame["url"].endswith("visual-evidence.html")
                for frame in manifest["frames"]
            )
            assert len(manifest["frames"]) == data["frames"]
            with Image.open(
                BytesIO(archive.read(manifest["frames"][0]["file"]))
            ) as frame:
                assert frame.size == (320, 200)
    else:
        artifact = tmp_path / "recording.webm"
        artifact.write_bytes(raw)
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(artifact),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0
        video = json.loads(stdout)
        assert video["streams"][0]["codec_name"] == "vp9"
        assert video["streams"][0]["width"] == 320
        assert float(video["format"]["duration"]) >= 0.2
        decoder = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(artifact),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
        )
        pixels, _ = await decoder.communicate()
        assert decoder.returncode == 0 and len(pixels) >= 320 * 200 * 3
        colors = [pixels[i : i + 3] for i in range(0, len(pixels), 320 * 200 * 3)]
        assert any(
            color[0] > 180 and color[1] < 50 and color[2] < 50 for color in colors
        )
        assert any(color[2] > 180 and color[0] < 50 for color in colors)
    assert (await client.get("/record/status")).json()["data"]["frames"] == 0
    assert (await client.post("/record/stop")).json()["error"] == "record_not_started"


async def test_recording_limits_and_close(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, page="visual-evidence.html")
    response = await client.post(
        "/record/start", json={"format": "zip", "max_frames": 1}
    )
    assert response.is_success, response.text
    await evaluate(client, "document.body.style.background='blue'")
    await wait_frames(client, 1)
    stopped = await client.post("/record/stop")
    assert stopped.json()["data"]["frames"] == 1
    assert stopped.json()["data"]["reason"] == "limit"
    await client.post("/record/start", json={"format": "zip"})
    await client.post("/session/close", json={"force": True})
    assert (await client.get("/record/status")).json()["data"]["frames"] == 0
    await navigate(client, local_server, page="visual-evidence.html")
    assert (await client.post("/record/start", json={"format": "zip"})).is_success
    await client.post("/session/close", json={"force": True})


async def test_recording_expiry_and_closed_tab(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, page="visual-evidence.html")
    await client.post("/record/start", json={"format": "zip", "max_seconds": 1})
    await wait_frames(client, 1)
    async with asyncio.timeout(4):
        while (await client.get("/record/status")).json()["data"]["recording"]:
            await asyncio.sleep(0.05)
    response = await client.post("/record/stop")
    assert response.json()["data"]["reason"] == "duration_limit"
    started = await client.post("/record/start", json={"format": "zip"})
    await evaluate(client, "document.body.style.background='red'")
    await wait_frames(client, 1)
    await client.post("/tab/close", json={"tab_id": started.json()["data"]["tab_id"]})
    response = await client.post("/record/stop")
    assert response.is_success, response.text
    assert response.json()["data"]["reason"] == "page_closed"


async def test_recording_survives_navigation_and_cancelled_export(
    browser_context, local_server
):
    ctx = await browser_context.fork_session()
    try:
        await ctx.navigate(f"{local_server}/visual-evidence.html")
        await ctx.record_start(format="zip")
        async with asyncio.timeout(5):
            while (await ctx.record_status())["frames"] < 1:
                await asyncio.sleep(0.02)
        before = (await ctx.record_status())["frames"]
        await ctx.navigate(f"{local_server}/input-actions.html")
        await ctx.evaluate("document.body.style.background='blue'")
        async with asyncio.timeout(5):
            while (await ctx.record_status())["frames"] <= before:
                await asyncio.sleep(0.02)
        result = await ctx.record_stop()
        with ZipFile(BytesIO(base64.b64decode(result["base64"]))) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert any(
                frame["url"].endswith("input-actions.html")
                for frame in manifest["frames"]
            )
        await ctx.record_start(format="webm")
        await ctx.evaluate("document.body.style.background='green'")
        async with asyncio.timeout(5):
            while (await ctx.record_status())["frames"] < 1:
                await asyncio.sleep(0.02)
        export = asyncio.create_task(ctx.record_stop())
        await asyncio.sleep(0.02)
        export.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await export
        assert not (await ctx.record_status())["recording"]
        # Cancellation cannot leave a session stuck in record_already_started.
        await ctx.record_start(format="zip")
    finally:
        await ctx.close()

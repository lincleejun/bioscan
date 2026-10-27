"""Previews and metadata (docs/gardener/contract.md §4). Originals are only read: previews go to the
paths the caller gives (the preview cache), never next to an original.

Metadata comes from exiftool (batched, -json -n) when it is installed, else from Pillow and BioScan's
EXIF readers (bioscan.service.decode) for photos; videos are probed with ffprobe. Without ffprobe or
ffmpeg, video fields stay None and a video preview fails with the reason. `playable` is a hint from
the container and codec (H.264 / HEVC / VP9 / AV1 in MP4 / MOV / M4V), not a test in a browser."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from bioscan.formats import is_raw
from bioscan.gardener.scan import VIDEO_EXT

THUMB_EDGE, LARGE_EDGE = 360, 1600
EXIFTOOL_BATCH = 200
PLAYABLE_CODECS = frozenset({"h264", "hevc", "vp9", "av1"})
PLAYABLE_CONTAINERS = frozenset({".mp4", ".mov", ".m4v"})
FLIP_TO_ORIENTATION = {0: 1, 3: 3, 5: 8, 6: 6}      # LibRaw sizes.flip -> EXIF orientation (= decode's)
META_KEYS = ("taken_at", "camera", "lens", "width", "height", "orientation", "lat", "lon",
             "duration_s", "video_codec", "fps", "playable")


@dataclass
class Preview:
    ok: bool
    method: str | None          # raw-embedded | raw-decoded | image | video-frame | None
    error: str | None
    meta: dict[str, Any] = field(default_factory=dict)   # source_width, source_height of the image previewed


def _tool(name: str) -> str | None:
    return shutil.which(name)


# ---- metadata -------------------------------------------------------------------------------------

def _iso(value: Any) -> str | None:
    """EXIF '2025:03:01 08:12:33[.37][-08:00]' -> '2025-03-01T08:12:33[.37][-08:00]'; None if not a date."""
    s = str(value or "").strip()
    if len(s) < 19 or s.startswith("0000"):
        return None
    return f"{s[0:4]}-{s[5:7]}-{s[8:10]}T{s[11:]}"


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _upright(w: Any, h: Any, orientation: int | None) -> tuple[int | None, int | None]:
    """Stored size -> upright size (orientations 5-8 turn the frame a quarter)."""
    w, h = (int(w) if _num(w) else None), (int(h) if _num(h) else None)
    return (h, w) if orientation in (5, 6, 7, 8) else (w, h)


def _camera(make: Any, model: Any) -> str | None:
    make, model = str(make or "").strip(), str(model or "").strip()
    if model.lower().startswith(make.lower()):
        make = ""
    return " ".join(x for x in (make, model) if x) or None


def _gps(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    lat, lon = _num(lat), _num(lon)
    if lat is None or lon is None or not (abs(lat) <= 90 and abs(lon) <= 180) or (lat == 0 and lon == 0):
        return None, None
    return lat, lon


def _empty() -> dict[str, Any]:
    return dict.fromkeys(META_KEYS)


def _from_exiftool(r: dict[str, Any]) -> dict[str, Any]:
    out = _empty()
    out["taken_at"] = _iso(r.get("SubSecDateTimeOriginal") or r.get("DateTimeOriginal")
                           or r.get("CreateDate"))
    if out["taken_at"] and r.get("OffsetTimeOriginal") and not r.get("SubSecDateTimeOriginal") \
            and r.get("DateTimeOriginal"):
        out["taken_at"] += str(r["OffsetTimeOriginal"]).strip()
    out["camera"] = _camera(r.get("Make"), r.get("Model"))
    lens = r.get("LensModel") or r.get("LensID") or r.get("Lens")
    out["lens"] = lens if isinstance(lens, str) and lens.strip() else None
    orientation = r.get("Orientation")
    out["orientation"] = int(orientation) if _num(orientation) else None
    size = [r.get("ExifImageWidth"), r.get("ExifImageHeight")]
    if not all(_num(x) for x in size) and isinstance(r.get("DefaultCropSize"), str):
        size = [float(x) for x in r["DefaultCropSize"].split()[:2]]
    if not all(_num(x) for x in size):
        size = [r.get("ImageWidth"), r.get("ImageHeight")]
    out["width"], out["height"] = _upright(*size, out["orientation"])
    out["lat"], out["lon"] = _gps(r.get("GPSLatitude"), r.get("GPSLongitude"))
    return out


def _argfile(paths: list[str]) -> list[str]:
    """The paths safe for exiftool's one-per-line argfile: a line break would start a new
    argument (an injected option such as -all=), so such a path is left to the per-file reader."""
    return [p for p in paths if "\n" not in p and "\r" not in p]


def _exiftool(paths: list[str]) -> dict[str, dict[str, Any]]:
    """exiftool over `paths` in batches; paths go on stdin (no argument-length limit). A path
    exiftool could not read is left out."""
    out: dict[str, dict[str, Any]] = {}
    tags = ["-SubSecDateTimeOriginal", "-DateTimeOriginal", "-OffsetTimeOriginal", "-CreateDate", "-Make",
            "-Model", "-LensModel", "-LensID", "-Lens", "-Orientation", "-ExifImageWidth", "-ExifImageHeight",
            "-DefaultCropSize", "-ImageWidth", "-ImageHeight", "-GPSLatitude", "-GPSLongitude"]
    for i in range(0, len(paths), EXIFTOOL_BATCH):
        batch = paths[i:i + EXIFTOOL_BATCH]
        try:
            p = subprocess.run([_tool("exiftool") or "exiftool", "-json", "-n", "-q", "-fast2", "-charset",
                                "filename=utf8", *tags, "-@", "-"], input="\n".join(batch).encode(),
                               capture_output=True, timeout=600)
            rows = json.loads(p.stdout or b"[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        for r in rows:
            src = r.get("SourceFile")
            if src in batch:
                out[src] = _from_exiftool(r)
    return out


def _from_file(path: str) -> dict[str, Any]:
    """Without exiftool: BioScan's EXIF readers for time, place and camera; Pillow (rasters) or
    LibRaw (RAW) for size and orientation; the EXIF lens model."""
    from bioscan.service import decode

    out = _empty()
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            f.seek(0)
            # a TIFF-based RAW keeps its IFDs at the front (decode reads only that much too)
            data = f.read(decode._TIFF_SCAN) if is_raw(path) and head in decode._TIFF else f.read()
        base, exif_ifd, gps = decode._metadata(data)
        out["lat"], out["lon"] = _gps(*decode.gps_from_ifd(gps))
        out["taken_at"] = decode.taken_at_from(exif_ifd, base)
        out["camera"] = decode.camera_from(base)
        lens = exif_ifd.get(0xA434)
        out["lens"] = str(lens).strip("\x00 ") or None if lens else None
        if is_raw(path):
            import rawpy

            with rawpy.imread(path) as raw:
                o = FLIP_TO_ORIENTATION.get(raw.sizes.flip, 1)
                out["orientation"] = o
                out["width"], out["height"] = _upright(raw.sizes.width, raw.sizes.height, o)
        else:
            with Image.open(path) as im:
                o = int(im.getexif().get(0x0112, 1) or 1)
                out["orientation"] = o
                out["width"], out["height"] = _upright(*im.size, o)
    except Exception:  # noqa: BLE001 - unreadable metadata is normal: the fields stay None
        pass
    return out


def _ffprobe(path: str) -> dict[str, Any]:
    out = _empty()
    tool = _tool("ffprobe")
    if tool is None:
        return out
    try:
        p = subprocess.run([tool, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                           capture_output=True, timeout=60)
        info = json.loads(p.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return out
    fmt = info.get("format") or {}
    video = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), None)
    try:
        out["duration_s"] = round(float(fmt.get("duration")), 3)
    except (TypeError, ValueError):
        pass
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    out["taken_at"] = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    out["camera"] = _camera(tags.get("com.apple.quicktime.make"), tags.get("com.apple.quicktime.model"))
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    if isinstance(loc, str):
        m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc)
        if m:
            out["lat"], out["lon"] = _gps(float(m.group(1)), float(m.group(2)))
    if video:
        codec = video.get("codec_name")
        out["video_codec"] = codec
        rotation = 0
        for sd in video.get("side_data_list") or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"])
        rotation = rotation or int((video.get("tags") or {}).get("rotate", 0) or 0)
        w, h = video.get("width"), video.get("height")
        out["width"], out["height"] = (h, w) if abs(rotation) % 180 == 90 else (w, h)
        try:
            n, d = str(video.get("avg_frame_rate") or "0/0").split("/")
            out["fps"] = round(int(n) / int(d), 3) if int(d) else None
        except ValueError:
            pass
        out["playable"] = codec in PLAYABLE_CODECS and Path(path).suffix.lower() in PLAYABLE_CONTAINERS
    return out


def read_meta(paths: list[str]) -> dict[str, dict[str, Any]]:
    """path -> {META_KEYS}: taken_at (ISO 8601, with the UTC offset when the file has one), camera,
    lens, upright width / height, orientation, lat, lon; for videos also duration_s, video_codec, fps
    and playable (a hint: None when ffprobe is missing). A field the file does not have is None;
    never raises."""
    videos = {p for p in paths if Path(p).suffix.lower() in VIDEO_EXT}
    photos = [p for p in paths if p not in videos]
    out: dict[str, dict[str, Any]] = {}
    if photos and _tool("exiftool"):
        out.update(_exiftool(_argfile(photos)))
    for p in photos:
        if p not in out:
            out[p] = _from_file(p)
    for p in videos:
        out[p] = _ffprobe(p)
    return {p: out[p] for p in paths}


# ---- previews -------------------------------------------------------------------------------------

def _orient(image: Image.Image, orientation: int) -> Image.Image:
    """Turn an image stored with EXIF `orientation` upright."""
    ops = {2: [Image.Transpose.FLIP_LEFT_RIGHT], 3: [Image.Transpose.ROTATE_180],
           4: [Image.Transpose.FLIP_TOP_BOTTOM], 5: [Image.Transpose.TRANSPOSE], 6: [Image.Transpose.ROTATE_270],
           7: [Image.Transpose.TRANSVERSE], 8: [Image.Transpose.ROTATE_90]}
    for op in ops.get(orientation, []):
        image = image.transpose(op)
    return image


def _upright_size(size: tuple[int, int], orientation: int) -> tuple[int, int]:
    return (size[1], size[0]) if orientation in (5, 6, 7, 8) else size


def _raw_embedded(path: str) -> tuple[Image.Image, tuple[int, int]] | None:
    """The RAW's embedded preview, upright: its own EXIF orientation when it has one, else the RAW's
    (LibRaw sizes.flip), and its full upright size. None when there is none or it is smaller than a
    thumbnail."""
    import rawpy

    with rawpy.imread(path) as raw:
        flip = FLIP_TO_ORIENTATION.get(raw.sizes.flip, 1)
        thumb = raw.extract_thumb()
    if thumb.format == rawpy.ThumbFormat.JPEG:
        im = Image.open(io.BytesIO(thumb.data))
        own = int(im.getexif().get(0x0112, 1) or 1)
        size = _upright_size(im.size, own if own != 1 else flip)     # before draft scales it down
        im.draft("RGB", (LARGE_EDGE, LARGE_EDGE))
        image = ImageOps.exif_transpose(im) if own != 1 else _orient(im, flip)
    else:
        image = _orient(Image.fromarray(thumb.data), flip)
        size = image.size
    return (image.convert("RGB"), size) if max(size) >= THUMB_EDGE else None


def _raw_decoded(path: str) -> Image.Image:
    """A half-size demosaic (LibRaw turns it upright itself)."""
    import rawpy

    with rawpy.imread(path) as raw:
        pixels = raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
    return Image.fromarray(pixels).convert("RGB")


def _raster(path: str) -> tuple[Image.Image, tuple[int, int]]:
    """Upright image (decoded at a reduced scale when the format allows) and its full upright size."""
    with Image.open(path) as im:
        size = _upright_size(im.size, int(im.getexif().get(0x0112, 1) or 1))
        im.draft("RGB", (LARGE_EDGE, LARGE_EDGE))      # JPEG: decode at a reduced scale, much faster
        return ImageOps.exif_transpose(im).convert("RGB"), size


def _video_frame(path: str, work: Path) -> tuple[Image.Image, tuple[int, int]]:
    """A frame at ~1 s (or 10 % of a shorter clip), else the first frame, and the video's upright
    size (ffprobe; else the frame's); ffmpeg turns it upright."""
    ffmpeg = _tool("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg 不可用：无法生成视频封面")
    probe = _ffprobe(path)
    duration = probe["duration_s"]
    at = min(1.0, duration * 0.1) if duration else 1.0
    errors = []
    for t in dict.fromkeys((round(at, 3), 0.0)):
        fd, tmp = tempfile.mkstemp(suffix=".png", dir=work)
        os.close(fd)
        try:
            p = subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", str(t), "-i", path, "-frames:v", "1",
                                "-vf", f"scale='min({LARGE_EDGE},iw)':'min({LARGE_EDGE},ih)':force_original_aspect_ratio=decrease",
                                tmp], capture_output=True, timeout=120)
            if p.returncode == 0 and os.path.getsize(tmp) > 0:
                with Image.open(tmp) as im:
                    frame = im.convert("RGB")
                return frame, ((probe["width"], probe["height"]) if probe["width"] else frame.size)
            errors.append(p.stderr.decode(errors="replace").strip()[-300:] or f"ffmpeg exit {p.returncode}")
        finally:
            os.unlink(tmp)
    raise RuntimeError(f"ffmpeg 取帧失败：{errors[-1]}")


def _save(image: Image.Image, edge: int, dest: Path) -> None:
    """A JPEG with long edge <= `edge` (never enlarged), written atomically."""
    copy = image.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    fd, tmp = tempfile.mkstemp(suffix=".jpg.part", dir=dest.parent)
    os.close(fd)
    try:
        copy.save(tmp, "JPEG", quality=85)
        os.replace(tmp, dest)
    except BaseException:
        os.unlink(tmp)
        raise


def make_preview(primary: str, media: str, thumb_path: Path, large_path: Path) -> Preview:
    """Writes thumb_path (long edge 360) and large_path (long edge 1600; smaller when the source is),
    JPEG. RAW: the embedded preview, else a half-size decode; JPEG / PNG / TIFF: Pillow, upright by
    EXIF; video: an ffmpeg frame. Never raises: a failure is ok=False with the reason."""
    thumb_path, large_path = Path(thumb_path), Path(large_path)
    src_dir = Path(primary).resolve().parent
    if src_dir in (thumb_path.resolve().parent, large_path.resolve().parent):
        return Preview(False, None, "预览路径位于原片目录：拒绝写入", {})
    try:
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        large_path.parent.mkdir(parents=True, exist_ok=True)
        got, method, notes = None, None, {}
        if media == "video":
            got, method = _video_frame(primary, large_path.parent), "video-frame"
        elif is_raw(primary):
            try:
                got, method = _raw_embedded(primary), "raw-embedded"
            except Exception as exc:  # noqa: BLE001 - fall back to decoding, say why
                notes["embedded_error"] = f"{type(exc).__name__}: {exc}"
            if got is None:
                image = _raw_decoded(primary)
                got, method = (image, image.size), "raw-decoded"
        else:
            got, method = _raster(primary), "image"
        image, (w, h) = got
        _save(image, LARGE_EDGE, large_path)
        _save(image, THUMB_EDGE, thumb_path)
        return Preview(True, method, None, {"source_width": w, "source_height": h, **notes})
    except Exception as exc:  # noqa: BLE001 - contract: never raise
        return Preview(False, None, f"{type(exc).__name__}: {exc}", {})

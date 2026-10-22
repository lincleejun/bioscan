"""People (docs/gardener/contract.md 单元 3): OpenCV YuNet finds faces and their five landmarks, SFace
turns each aligned face into a 128-d identity feature, and `cluster` groups the features of a task
by person. Both models are small ONNX files pinned on the Hugging Face hub (YuNet MIT, SFace
Apache-2.0) and run on the CPU; features stay in the local database and never leave the machine.

The input is Gardener's own upright preview (long edge 1600), not the original: faces need no
full-resolution decode, and the preview is already rotated by EXIF."""
from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import Any

import numpy as np

YUNET = ("opencv/face_detection_yunet", "face_detection_yunet_2023mar.onnx", "3cc26e7f1014a5ee5d74a42acee58bafc9d0a310")
SFACE = ("opencv/face_recognition_sface", "face_recognition_sface_2021dec.onnx", "3d7082438a6e4551e840c9b2bb60b71e8da4b524")
MIN_SCORE = 0.6         # YuNet confidence a face needs to be kept at all
MIN_FACE = 24           # px, shorter side of the face box on the preview
STRONG_SCORE = 0.85     # a face this sure and this big carries enough identity to start or join a cluster
STRONG_FACE = 40
SAME_PERSON = 0.40      # cosine between a face and a person's mean feature (SFace's own cut is 0.363)
LOOSE = 0.20            # a leftover face (profile, sunglasses, small) may join an established person at this
MARGIN = 0.08           # ... when the next closest person is this much further away


def strong(face: dict) -> bool:
    """Results from before weak faces were kept have no `px`: they were all strong."""
    return face.get("score", 0) >= STRONG_SCORE and face.get("px", STRONG_FACE) >= STRONG_FACE


def version() -> str:
    return f"yunet@{YUNET[2][:12]};sface@{SFACE[2][:12]}"


def encode(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float16).tobytes()).decode()


def decode(s: str) -> np.ndarray:
    v = np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


class FaceModel:
    """Loads both models on first use (download through the hub cache once, then offline)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._det: Any = None
        self._rec: Any = None
        self.version = version()

    def _load(self) -> None:
        import cv2
        from huggingface_hub import hf_hub_download

        det = hf_hub_download(YUNET[0], YUNET[1], revision=YUNET[2])
        rec = hf_hub_download(SFACE[0], SFACE[1], revision=SFACE[2])
        self._det = cv2.FaceDetectorYN.create(det, "", (320, 320), MIN_SCORE, 0.3, 5000)
        self._rec = cv2.FaceRecognizerSF.create(rec, "")

    def ensure(self) -> None:
        with self._lock:
            if self._det is None:
                self._load()

    def faces(self, image_path: str | Path) -> list[dict[str, Any]]:
        """Faces on one upright image: {box: [x0, y0, x1, y1] as fractions, score, vec (base64 f16, unit
        length)}, largest first. Raises when the image cannot be read."""
        from PIL import Image

        self.ensure()
        with Image.open(image_path) as im:
            rgb = np.asarray(im.convert("RGB"))
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        h, w = bgr.shape[:2]
        with self._lock:                     # the OpenCV objects keep per-call state
            self._det.setInputSize((w, h))
            _, found = self._det.detect(bgr)
            out = []
            for f in [] if found is None else found:
                x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
                if min(fw, fh) < MIN_FACE or score < MIN_SCORE:
                    continue
                feat = self._rec.feature(self._rec.alignCrop(bgr, f)).reshape(-1)
                feat = feat / (float(np.linalg.norm(feat)) or 1.0)
                out.append({"box": [round(max(x, 0) / w, 4), round(max(y, 0) / h, 4), round(min(x + fw, w) / w, 4),
                                    round(min(y + fh, h) / h, 4)],
                            "score": round(score, 4), "px": int(min(fw, fh)), "vec": encode(feat)})
        return sorted(out, key=lambda d: -(d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]))


def cluster(faces: list[tuple[str, str, float]], threshold: float = SAME_PERSON) -> list[list[str]]:
    """faces: (asset_id, vec, weight) for every face of a task. Greedy: the best faces (highest weight)
    open people first; each face joins the person whose mean feature is closest when the cosine
    reaches `threshold`, else opens a new person. Returns each person's asset ids (unique, in input
    order), largest person first; the order of `faces` decides ties, so the result is deterministic."""
    if not faces:
        return []
    order = sorted(range(len(faces)), key=lambda i: (-faces[i][2], i))
    vecs = np.stack([decode(faces[i][1]) for i in order])
    sums: list[np.ndarray] = []
    members: list[list[int]] = []
    for k, v in enumerate(vecs):
        if sums:
            cents = np.stack(sums)
            cents /= np.linalg.norm(cents, axis=1, keepdims=True)
            sims = cents @ v
            j = int(np.argmax(sims))
            if sims[j] >= threshold:
                sums[j] = sums[j] + v
                members[j].append(order[k])
                continue
        sums.append(v.copy())
        members.append([order[k]])
    people = []
    for m in members:
        ids: list[str] = []
        for i in sorted(m):
            if faces[i][0] not in ids:
                ids.append(faces[i][0])
        people.append(ids)
    return sorted(people, key=lambda ids: -len(ids))


def centroid(vecs: list[str]) -> np.ndarray:
    c = np.mean(np.stack([decode(v) for v in vecs]), axis=0)
    return c / (float(np.linalg.norm(c)) or 1.0)


def nearest(vec: str, people: dict[str, np.ndarray], threshold: float = SAME_PERSON) -> str | None:
    """The named person whose mean confirmed face is closest to `vec`, when the cosine reaches `threshold`."""
    if not people:
        return None
    v = decode(vec)
    key, best = max(((k, float(c @ v)) for k, c in people.items()), key=lambda kv: kv[1])
    return key if best >= threshold else None


def shared_face(per_asset: dict[str, list[str]]) -> dict[str, int]:
    """asset id -> index of its face that belongs to the one person these assets share (the photos of one
    person group): start from every asset's largest face (index 0), then twice take each asset's face
    closest to the mean of the chosen ones."""
    pick = {aid: 0 for aid, vs in per_asset.items() if vs}
    for _ in range(2):
        c = centroid([per_asset[a][i] for a, i in pick.items()]) if pick else None
        if c is None:
            break
        pick = {a: int(np.argmax([float(decode(v) @ c) for v in per_asset[a]])) for a in pick}
    return pick


def attach(vec: str, people: dict[str, np.ndarray]) -> str | None:
    """The established person a leftover face most likely is: cosine >= LOOSE and at least MARGIN ahead of
    the next person (two people that look alike to the model get neither)."""
    if not people:
        return None
    v = decode(vec)
    sims = sorted(((float(c @ v), k) for k, c in people.items()), reverse=True)
    best, key = sims[0]
    runner = sims[1][0] if len(sims) > 1 else -1.0
    return key if best >= LOOSE and best - runner >= MARGIN else None

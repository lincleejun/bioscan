"""New XMP sidecars, shared by `bioscan geotag --xmp` and `bioscan cull --xmp` (standard library only).

A sidecar is written only where the photo has none (`<stem>.xmp` for Lightroom, Capture One and Bridge,
or darktable/digiKam's `<name>.<ext>.xmp`): an existing one (the editor's develop settings, keywords,
the owner's own stars) is never touched or merged, and the photo file itself is never written. Reading
XMP ratings back is `bioscan.aesthetic.parse_xmp`."""
from __future__ import annotations

from pathlib import Path

WRITTEN, EXISTS = "written", "exists"


def packet(tool: str, attrs: list[str]) -> str:
    """One XMP packet whose rdf:Description carries `attrs` (namespace declarations and simple
    properties, each already `name="escaped value"`), one per line."""
    body = "".join(f"\n   {a}" for a in attrs)
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{tool}">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            f'  <rdf:Description rdf:about=""{body}/>\n'
            ' </rdf:RDF>\n'
            '</x:xmpmeta>\n'
            '<?xpacket end="w"?>\n')


def sidecar_paths(photo: str) -> tuple[Path, Path]:
    """(stem.xmp: Lightroom, Capture One, Bridge; name.ext.xmp: darktable, digiKam)."""
    p = Path(photo)
    return p.with_suffix(".xmp"), p.with_name(p.name + ".xmp")


def write_new(photo: str, text: str) -> str:
    """Write stem.xmp when neither sidecar exists: WRITTEN, else EXISTS (nothing written)."""
    ours, other = sidecar_paths(photo)
    if ours.exists() or other.exists():
        return EXISTS
    try:
        with open(ours, "x", encoding="utf-8") as f:       # exclusive create: no race with a writer
            f.write(text)
    except FileExistsError:
        return EXISTS
    return WRITTEN

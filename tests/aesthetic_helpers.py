"""Aesthetic heads and XMP packets for tests (standard library)."""
import math
import random

from bioscan import aesthetic as aes

NS = ('xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/"')


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def random_head(seed=0, name="test-head", lo=0.0, hi=10.0, **prov):
    """A valid head with seeded random weights, mean 0 and std 1/sqrt(768)."""
    r = random.Random(seed)
    w = [r.gauss(0, 1) for _ in range(aes.DIM)]
    return aes.make_head(name, w, (lo + hi) / 2, [0.0] * aes.DIM, [1 / math.sqrt(aes.DIM)] * aes.DIM, lo, hi,
                         "test", {"data": "synthetic", "licence": "CC0", "n": 0, "date": "2026-09-24", **prov})


def xmp(rating=None, label=None, pick=None, element=False):
    """An XMP packet with the given properties, as attributes of rdf:Description (Lightroom's way)
    or as child elements (another valid serialisation)."""
    props = [(k, v) for k, v in (("xmp:Rating", rating), ("xmp:Label", label), ("xmpDM:pick", pick)) if v is not None]
    if element:
        desc = f'<rdf:Description rdf:about="" {NS}>' + "".join(f"<{k}>{v}</{k}>" for k, v in props) + \
            "</rdf:Description>"
    else:
        desc = f'<rdf:Description rdf:about="" {NS} ' + " ".join(f'{k}="{v}"' for k, v in props) + "/>"
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            f"  {desc}\n </rdf:RDF>\n</x:xmpmeta>\n<?xpacket end=\"w\"?>")

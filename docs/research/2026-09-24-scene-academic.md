# Scene / photo-content taxonomies and zero-shot prompt design — primary-source notes

Date: 2026-09-24. Factual only. Except where marked "via fetch-tool summary", every number below was read from the file or PDF named; items marked
**unverified** could not be confirmed from a primary source. Local copies of all fetched files are in
this scratchpad folder (`categories_places365.txt`, `IO_places365.txt`, `places_hier.csv`, `sun.txt`,
`sun_ijcv.txt`, `sun_hier_wb.html`, `ava.txt`, `ava_tags.txt`, `clip.txt`, `prompts.md`, `openclip_results.csv`, `pe.py`, `pec.py`).

---

## 1. Places365 (MIT CSAIL)

Sources
- Category list: https://raw.githubusercontent.com/CSAILVision/places365/master/categories_places365.txt (365 lines, indices 0-364; `wc -l` prints 364 because the last line `/z/zen_garden 364` has no trailing newline, verified with `od -c`; the IO file and the hierarchy sheet both list 365).
- Indoor/outdoor file: https://raw.githubusercontent.com/CSAILVision/places365/master/IO_places365.txt. Encoding per `run_placesCNN_unified.py` line 48: `labels_IO.append(int(items[-1]) -1) # 0 is indoor, 1 is outdoor`, i.e. file value **1 = indoor, 2 = outdoor**. Counts: **161 indoor, 204 outdoor**.
- Scene hierarchy: the repo README (line 55) says "The scene hierarchy is listed at [here](https://docs.google.com/spreadsheets/d/1H7ADoEIGgbF_eXh9kcJjCs5j_r3VJwke4nebhkdzksg/edit?usp=sharing), with a simple browswer at [here](http://places2.csail.mit.edu/scene_hierarchy.html)". The download page http://places2.csail.mit.edu/download.html links the same sheet as "Scene hierarchy". It is a live, unversioned Google Sheet; exported with `.../export?format=csv` (366 rows = 2 header rows + 365 categories; local `places_hier.csv`). The 3-level hierarchy file is NOT in the GitHub repo (no `scene_hierarchy_places365.csv`; repo file list checked via the GitHub API).

Sheet structure (header row 1 / row 2, verbatim): `Level 1` = `indoor`, `outdoor, natural`, `outdoor, man-made`; then `Level 2, INDOOR:`, `Level 2, OUTDOOR NATURAL:`, `Level 2, OUTDOOR MAN-MADE:` with 16 columns. Each cell is 0/1, so a category can belong to more than one group: **33 categories are in two Level-1 groups and 63 are in two or more Level-2 groups; every category has at least one Level-2 group.** The counts below are therefore multi-label, not a partition (they sum to more than 365).

Level 1 (count of categories flagged 1):
| Level 1 | leaves | examples |
|---|---|---|
| indoor | 159 | airplane_cabin, airport_terminal, alcove, amusement_arcade, aquarium |
| outdoor, natural | 80 | aqueduct, badlands, bamboo_forest, barn, beach |
| outdoor, man-made | 159 | airfield, alley, amphitheater, amusement_park, apartment_building/outdoor |

Level 2 (column names verbatim from the sheet, including its spelling "millitary"):
| Level 1 | Level 2 (verbatim) | leaves | examples |
|---|---|---|---|
| indoor | shopping and dining | 44 | auto_showroom, bakery/shop, bank_vault, banquet_hall, bar |
| indoor | workplace (office building, factory, lab, etc.) | 35 | assembly_line, atrium/public, auto_factory, bank_vault, biology_laboratory |
| indoor | home or hotel | 31 | alcove, attic, basement, bathroom, bedchamber |
| indoor | transportation (vehicle interiors, stations, etc.) | 16 | airplane_cabin, airport_terminal, berth, bus_interior, bus_station/indoor |
| indoor | sports and leisure | 17 | amusement_arcade, arena/hockey, arena/performance, arena/rodeo, ball_pit |
| indoor | cultural (art, education, religion, millitary, law, politics, etc.) | 30 | aquarium, arcade, archive, art_gallery, art_school |
| outdoor natural | water, ice, snow | 33 | beach, boardwalk, boathouse, canal/natural, coast |
| outdoor natural | mountains, hills, desert, sky | 14 | badlands, butte, canyon, cliff, desert/sand |
| outdoor natural | forest, field, jungle | 31 | bamboo_forest, barn, boardwalk, botanical_garden, corn_field |
| outdoor natural | man-made elements | 32 | aqueduct, barn, boardwalk, boathouse, botanical_garden |
| outdoor man-made | transportation (roads, parking, bridges, boats, airports, etc.) | 25 | airfield, boat_deck, boathouse, bridge, bus_station/indoor |
| outdoor man-made | cultural or historical building/place (millitary, religious) | 31 | amphitheater, aqueduct, arch, archaelogical_excavation, army_base |
| outdoor man-made | sports fields, parks, leisure spaces | 36 | amusement_park, athletic_field/outdoor, baseball_field, beer_garden, boardwalk |
| outdoor man-made | industrial and construction | 11 | construction_site, dam, excavation, industrial_area, junkyard |
| outdoor man-made | houses, cabins, gardens, and farms | 37 | balcony/exterior, balcony/interior, barn, barndoor, beach_house |
| outdoor man-made | commercial buildings, shops, markets, cities, and towns | 37 | alley, apartment_building/outdoor, balcony/exterior, balcony/interior, bazaar/outdoor |

(Sum of Level-2 counts = 460 over 365 categories because of multi-membership. Indoor L2 sum 173 vs 159 L1-indoor, etc.)

---

## 2. SUN397 / SUN database hierarchy

Sources
- CVPR 2010 paper "SUN Database: Large-scale Scene Recognition from Abbey to Zoo" (Xiao, Hays, Ehinger, Oliva, Torralba): https://3dvision.princeton.edu/projects/2010/SUN/paper.pdf (fetched; local `sun.txt`).
- IJCV 2014 paper "SUN Database: Exploring a Large Collection of Scene Categories" (Xiao, Ehinger, Hays, Torralba, Oliva): https://faculty.cc.gatech.edu/~hays/papers/SUN_IJCV.pdf (fetched, 22 pages; local `sun_ijcv.txt`). The Princeton copies (vision.princeton.edu/projects/2010/SUN/paperIJCV.pdf and its redirect to www.cs.princeton.edu/research/areas/gravisprojects/2010/SUN/) return the department 404 page.
- The full hierarchy URL printed in IJCV Fig. 23, `http://vision.princeton.edu/projects/2010/SUN/hierarchy/`, and the whole `vision.princeton.edu/projects/2010/SUN/` tree are dead (404). `groups.csail.mit.edu/vision/SUN/` (which sun.cs.princeton.edu iframes) is an empty directory listing. A Wayback Machine snapshot of the hierarchy browser exists and was fetched: http://web.archive.org/web/20250326025345/https://vision.princeton.edu/projects/2010/SUN/hierarchy/ (local `sun_hier_wb.html`). It is the full SUN hierarchy (1051 leaf cells, 904 distinct leaf names, i.e. the ~908-category SUN, with duplicates across groups), not a SUN397-specific file. Per-group SUN397 counts below are **derived** by intersecting that page's leaf lists with the 397 SUN397 class names in openai/CLIP `prompts.md`; all 397 names were matched.

Verbatim, CVPR 2010 (Sec. 4, human scene classification): "we group the 397 scene categories in a 3-level tree, and the participants navigate through an overcomplete three-level hierarchy to arrive at a specific scene type (e.g. "bedroom") by making relatively easy choices (e.g. "indoor" versus "outdoor natural" versus "outdoor man-made" at the first level). Many categories such as "hayfield" are duplicated in the hierarchy because there might be confusion over whether such a category belongs in the natural or man-made sub-hierarchies. This hierarchy is used strictly as a human organizational tool, and plays no roll in our experimental evaluations."

Verbatim, IJCV 2014 (human scene classification section, Fig. 10 interface): "The 3-level tree contains then 397 leaf nodes (SUN categories) connected to 15 parent nodes at the second level that are in turn connected to three nodes at the first level (super-ordinate categories). The mid-level categories were selected to be easily interpreted by workers, have minimal overlap, and provide a fairly even split of the images in each super-ordinate category. When there was any confusion about the best super-ordinate category for a category (e.g., "hayfield" could be considered natural or man-made), the category was included in both super-ordinate categories."

But IJCV Sec. 5.2 "Scene Categorization" says "we train a 16-way classifier at the second level of the scene hierarchy", and Fig. 23 ("The first two levels of a hierarchy of scene categories") prints **16** second-level labels. Discrepancy (15 vs 16) is in the paper itself; not resolved here.

Fig. 23 labels as extracted by pdftotext (short forms; full forms appear in the Fig. 10 annotation-interface screenshot, e.g. "workplace (office building, factory, lab, etc.)", "transportation (vehicle interiors, stations, etc.)", "cultural (art, education, religion, etc.)"):
- outdoor natural (4): water & snow; mountains & desert; forest & field; man-made
- outdoor manmade (6): transportation; historical place; parks; industrial; houses & gardens; commercial markets
- indoor (6): shopping & dining; workplace; home & hotel; vehicle interior; sports & leisure; cultural

Archived hierarchy browser (Wayback snapshot above), Level-1 and Level-2 names verbatim (same 16 names and the same "millitary" spelling as the Places365 sheet), with leaf counts for the full SUN page and for the SUN397 subset (derived; multi-label, a leaf can sit in several groups):
| Level 1 | Level 2 (verbatim) | full-SUN leaves | SUN397 leaves | SUN397 examples |
|---|---|---|---|---|
| indoor | shopping and dining | 92 | 40 | bakery shop, banquet hall, bar, bazaar indoor |
| indoor | workplace (office building, factory, lab, etc.) | 110 | 40 | anechoic chamber, assembly line, atrium public, auto factory |
| indoor | home or hotel | 57 | 35 | attic, basement, bathroom, bedroom |
| indoor | transportation (vehicle interiors, stations, etc.) | 58 | 21 | airplane cabin, airport terminal, baggage claim, berth |
| indoor | sports and leisure | 52 | 22 | amusement arcade, badminton court indoor, ball pit, ballroom |
| indoor | cultural (art, education, religion, millitary, law, politics, etc.) | 76 | 36 | apse indoor, aquarium, archive, art gallery |
| outdoor natural | water, ice, snow | 53 | 35 | bayou, beach, boardwalk, boathouse |
| outdoor natural | mountains, hills, desert, sky | 27 | 14 | badlands, butte, canyon, cavern indoor |
| outdoor natural | forest, field, jungle | 47 | 38 | bamboo forest, barn, bayou, boardwalk |
| outdoor natural | man-made elements | 42 | 39 | aqueduct, barn, bayou, boardwalk |
| outdoor man-made | transportation (roads, parking, bridges, boats, airports, etc.) | 92 | 27 | arrival gate outdoor, bayou, boat deck, boathouse |
| outdoor man-made | cultural or historical building/place (millitary, religious) | 71 | 37 | abbey, amphitheater, aqueduct, arch |
| outdoor man-made | sports fields, parks, leisure spaces | 84 | 40 | amusement park, athletic field outdoor, baseball field, basketball court outdoor |
| outdoor man-made | industrial and construction | 43 | 15 | construction site, dam, electrical substation, excavation |
| outdoor man-made | houses, cabins, gardens, and farms | 84 | 37 | balcony exterior, balcony interior, barn, barndoor |
| outdoor man-made | commercial buildings, shops, markets, cities, and towns | 63 | 34 | alley, apartment building outdoor, balcony exterior, balcony interior |

SUN397 Level-1 membership (derived, multi-label): indoor 177, outdoor natural 89, outdoor man-made 171 (sum 437 > 397 because of duplicated leaves such as hayfield/boardwalk/bayou).

The Places365 sheet (Section 1) uses exactly these 16 Level-2 names for its own 365 categories.

Other verbatim facts
- IJCV: "There are two categories, promenade deck and ticket booth, that are considered to be both indoor and outdoor in our scene hierarchy." Indoor-vs-outdoor classification with all features: "The overall performance is 94.2 %".
- CVPR 2010 human accuracy by group: "Within the hierarchy, indoor sports and leisure scenes are the most accurately classified (78.8%) while outdoor cultural and historical scenes were least accurately classified (49.6%)." Overall human 68%, best machine ("all features") 38%; "Computational performance is best for outdoor natural scenes (43.2%), and then indoor scenes (37.5%), and worst in outdoor man-made scenes (35.8%). Within the hierarchy, indoor transportation (vehicle interiors, stations, etc.) scenes are the most accurately classified (51.9%) while indoor shopping and dining scenes were least accurately classified (29.0%)."
- Class naming: SUN397 leaf names carry indoor/outdoor qualifiers where a place has both views (e.g. `apse indoor`, `apartment building outdoor`, `arrival gate outdoor`, `athletic field outdoor`, `atrium public`), see the CLIP class list below.

---

## 3. AVA (Murray, Marchesotti, Perronnin, CVPR 2012)

Source: https://refbase.cvc.uab.es/files/MMP2012a.pdf (fetched; local `ava.txt`).

Verbatim (Sec. 2): "Semantic annotations: We provide 66 textual tags describing the semantics of the images. Approximately 200,000 images contain at least one tag, and 150,000 images contain 2 tags. The frequency of the most common tags in the database can be observed in Figure 2."
- The 66 tag names are not listed in the paper text (Figure 2's axis labels are rotated and do not survive text extraction). They are in the AVA release file `tags.txt`; the release README says "Columns 13 - 14: Semantic tag IDs. There are 66 IDs ranging from 1 to 66. The file tags.txt contains the textual tag corresponding to the numerical id. Each image has between 0 and 2 tags." Read from the redistributed copy of the release at https://raw.githubusercontent.com/imfing/ava_downloader/master/AVA_dataset/tags.txt (66 lines; local `ava_tags.txt`), verbatim, in id order:
  1 Abstract; 2 Cityscape; 3 Fashion; 4 Family; 5 Humorous; 6 Interior; 7 Sky; 8 Snapshot; 9 Sports; 10 Urban; 11 Vintage; 12 Emotive; 13 Performance; 14 Landscape; 15 Nature; 16 Candid; 17 Portraiture; 18 Still Life; 19 Animals; 20 Architecture; 21 Black and White; 22 Macro; 23 Travel; 24 Action; 25 Photojournalism; 26 Nude; 27 Rural; 28 Water; 29 Studio; 30 Political; 31 Advertisement; 32 Persuasive; 33 Panoramic; 34 Digital Art; 35 Seascapes; 36 Traditional Art; 37 Diptych / Triptych; 38 Floral; 39 Transportation; 40 Food and Drink; 41 Science and Technology; 42 Wedding; 43 Astrophotography; 44 Military; 45 History; 46 Infrared; 47 Self Portrait; 48 Textures; 49 DPChallenge GTGs; 50 Children; 51 Blur; 52 Photo-Impressionism; 53 High Dynamic Range (HDR); 54 Texture Library; 55 Overlays; 56 Maternity; 57 Birds; 58 Horror; 59 Music; 60 Pinhole/Zone Plate; 61 Street; 62 Lensbaby; 63 Fish Eye; 64 Camera Phones; 65 Insects, etc; 66 Analog.
  (Provenance: dataset release file via a third-party mirror, not the paper.)
- Sec. 4.2: "We selected 8 semantic categories equivalent to the ones picked by [15]. These categories are also the 8 most popular semantic tags in AVA, and they contain on average 14,368 images." The 8 names are not printed in the text (only in Figure 9). **unverified.**

Verbatim (Sec. 2): "Photographic style annotations: Despite the lack of a formal definition, we understand photographic style as a consistent manner of shooting photographs achieved by manipulating camera configurations (such as shutter speed, exposure, or ISO level). We manually selected 72 Challenges corresponding to photographic styles and we identified three broad categories according to a popular photography manual [12]: Light, Colour, Composition. We then merged similar challenges (e.g. "Duotones" and "Black & White") and we associated each style with one category. The 14 resulting photographic styles along with the number of associated images are: Complementary Colors (949), Duotones (1,301), High Dynamic Range (396), Image Grain (840), Light on White (1,199), Long Exposure (845), Macro (1,698), Motion Blur (609), Negative Image (959), Rule of Thirds (1,031), Shallow DOF (710), Silhouettes (1,389), Soft Focus (1,479), Vanishing Point (674)."

The same 14 labels, as used by Karayev et al. 2014 ("Recognizing Image Style", arXiv 1311.3715, Sec. 6.3 / Table 3, fetched HTML): Complementary_Colors, Duotones, HDR, Image_Grain, Light_On_White, Long_Exposure, Macro, Motion_Blur, Negative_Image, Rule_of_Thirds, Shallow_DOF, Silhouettes, Soft_Focus, Vanishing_Point.

---

## 4. Zero-shot scene classification: prompts and reported accuracy

### 4.1 CLIP prompt templates for SUN397 (github.com/openai/CLIP, `data/prompts.md`, section `## SUN397`, line 2615 of the raw file)

Verbatim:
```
templates = [
    'a photo of a {}.',
    'a photo of the {}.',
]
```
Class names in that file are plain lower-case with spaces and indoor/outdoor suffixes, e.g. `'abbey', 'airplane cabin', 'airport terminal', 'alley', 'amphitheater', 'amusement arcade', 'amusement park', 'anechoic chamber', 'apartment building outdoor', 'apse indoor', 'aquarium', 'aqueduct', 'arch', 'archive', 'arrival gate outdoor', 'art gallery', 'art school', 'art studio', 'assembly line', 'athletic field outdoor', 'atrium public', 'attic', ...` (397 entries). There is no Places365 section in `prompts.md`.

### 4.2 CLIP paper (Radford et al. 2021, arXiv 2103.00020, fetched PDF; local `clip.txt`) — Sec. 3.1.4 prompt engineering, verbatim excerpts
- "we found that using the prompt template "A photo of a {label}." to be a good default that helps specify the text is about the content of the image. This often improves performance over the baseline of using only the label text. For instance, just using this prompt improves accuracy on ImageNet by 1.3%."
- "We found on several fine-grained image classification datasets that it helped to specify the category. For example on Oxford-IIIT Pets, using "A photo of a {label}, a type of pet." ... on satellite image classification datasets it helped to specify that the images were of this form and we use variants of "a satellite photo of a {label}."."
- "These classifiers are computed by using different context prompts such as 'A photo of a big {label}" and "A photo of a small {label}". We construct the ensemble over the embedding space instead of probability space. ... On ImageNet, we ensemble 80 different context prompts and this improves performance by an additional 3.5% over the single default prompt discussed above. When considered together, prompt engineering and ensembling improve ImageNet accuracy by almost 5%."
- Figure 5 (zero-shot CLIP vs. linear-probe ResNet-50, delta in accuracy): SUN397 **+7.8**.

CLIP paper Table 11 "Zero-shot performance of CLIP models over 27 datasets", SUN397 column (5th column: Food101, CIFAR10, CIFAR100, Birdsnap, SUN397, ...):
| model | SUN397 zero-shot |
|---|---|
| RN50 | 59.6 |
| RN101 | 59.9 |
| RN50x4 | 62.7 |
| RN50x16 | 65.0 |
| RN50x64 | 66.9 |
| ViT-B/32 | 63.2 |
| ViT-B/16 | 65.2 |
| ViT-L/14 | 67.7 |
| ViT-L/14-336px | 68.4 |
Column alignment check: open_clip's independent evaluation of the same checkpoints gives 0.6865 (L/14-336), 0.6756 (L/14), 0.6435 (B/16), 0.6248 (B/32), 0.5994 (RN50) — consistent to within 1 point.
Same paper, Table 10 (linear-probe, not zero-shot) SUN397: RN50 73.3, RN101 75.1, RN50x4 77.0, RN50x16 79.2, RN50x64 81.1, B/32 76.6, B/16 78.4, L/14 81.8, L/14-336px 82.2.

### 4.3 open_clip results CSV (https://raw.githubusercontent.com/mlfoundations/open_clip/main/docs/openclip_results.csv; last commit touching the file 2023-11-22; 121 rows; local copy)
Column `SUN397` (zero-shot top-1, fraction) and `ImageNet 1k`:
| name | pretrained | SUN397 | ImageNet 1k |
|---|---|---|---|
| ViT-SO400M-14-SigLIP-384 | webli | 0.7541 | 0.8308 |
| ViT-SO400M-14-SigLIP | webli | 0.7436 | 0.8203 |
| ViT-L-16-SigLIP-384 | webli | 0.7250 | 0.8207 |
| ViT-L-16-SigLIP-256 | webli | 0.7253 | 0.8045 |
| ViT-B-16-SigLIP-512 | webli | 0.7152 | 0.7914 |
| ViT-B-16-SigLIP-384 | webli | 0.7096 | 0.7849 |
| ViT-B-16-SigLIP-256 | webli | 0.7026 | 0.7653 |
| ViT-B-16-SigLIP | webli | 0.7001 | 0.7604 |
| ViT-B-16-SigLIP-i18n-256 | webli | 0.6978 | 0.7513 |
| ViT-L-14-336 | openai | 0.6865 | 0.7656 |
| ViT-L-14 | openai | 0.6756 | 0.7554 |
| ViT-B-16 | openai | 0.6435 | 0.6834 |
| ViT-B-32 | openai | 0.6248 | 0.6332 |
| RN50 | openai | 0.5994 | 0.5982 |
The CSV has no Places column and **no SigLIP2 rows** (it predates SigLIP2, Feb 2025). open_clip `docs/PRETRAINED.md` also has no SigLIP2 results table (grep returned nothing).

### 4.4 SigLIP 2 (arXiv 2502.14786)
- Paper: the full v1 HTML (https://arxiv.org/html/2502.14786v1, 558 KB, fetched with curl) contains **zero** occurrences of "SUN397" or "Places" (`grep -c -i -E 'sun397|places'` = 0); v2 HTML and the arxiv PDF were 404/blocked from this machine. Table 1 zero-shot classification (via fetch-tool summary of the v1 HTML; the ImageNet column matches the verbatim README rows below one-for-one, 74.0/79.1/82.5/84.1/83.4/84.5): B/32@256 ImageNet 74.0 / v2 66.9 / ReaL 81.4 / ObjectNet 66.1; B/16@256 79.1 / 72.5 / 85.4 / 74.5; L/16@256 82.5 / 76.8 / 87.3 / 83.0; So400m/14@384 84.1 / 78.7 / 88.1 / 86.0; So400m/16@256 83.4 / 77.8 / 87.7 / 84.8; g/16@256 84.5 / 79.2 / 88.3 / 87.1. The paper does not state its zero-shot prompt templates.
- Verbatim from the official checkpoint README (https://raw.githubusercontent.com/google-research/big_vision/main/big_vision/configs/proj/image_text/README_siglip2.md), columns `INet 0-shot | COCO T→I | COCO I→T`:
  B/32@256 74.0 | 47.2 | 63.7; B/16@224 78.2 | 52.1 | 68.9; B/16@256 79.1 | 53.2 | 69.7; B/16@384 80.6 | 54.6 | 71.4; B/16@512 81.2 | 55.2 | 71.2; L/16@256 82.5 | 54.7 | 71.5; L/16@384 83.1 | 55.3 | 71.4; L/16@512 83.5 | 55.2 | 72.1; So400m/14@224 83.2 | 55.1 | 71.5; So400m/14@384 84.1 | 55.8 | 71.7; So400m/16@256 83.4 | 55.4 | 71.5; So400m/16@384 84.1 | 56.0 | 71.2; So400m/16@512 84.3 | 56.0 | 71.3; g-opt/16@256 84.5 | 55.7 | 72.5; g-opt/16@384 85.0 | 56.1 | 72.8; NaFlex B/16 78.5 | 51.1 | 67.3; NaFlex So400m/16 83.5 | 55.1 | 71.2.
- **SigLIP2 zero-shot on SUN397 or Places365: not reported in the paper, the big_vision README, or the open_clip CSV. Absent.**
- **Places365 zero-shot in general: absent from CLIP Table 11 (its 27 datasets include SUN397 but not Places), from the open_clip results CSV (no Places column), and from SigLIP2. No primary zero-shot Places number was found for any of these models.**
- SigLIP (v1) zero-shot on SUN397: only the open_clip numbers above (third-party evaluation of the released weights); not checked against the SigLIP paper.

### 4.5 How SigLIP / SigLIP2 are prompted for zero-shot (primary code and docs)
- big_vision `evaluators/proj/image_text/prompt_engineering.py` (fetched): `get_prompt_templates()` selects `"clip_paper": CLIP_PAPER_PROMPT_TEMPLATES` (81 entries in big_vision's copy, counted; CLIP's own ImageNet list is described as 80) or `"clip_best": CLIP_BEST_PROMPT_TEMPLATES`; class names come from `get_class_names(*, dataset_name, source="dataset_info", canonicalize=True)`, i.e. TFDS dataset info by default, with `source="clip"` opting into `imagenet_class_names.CLIP_IMAGENET_CLASS_NAMES`; names are passed through `canonicalize_text`, which (verbatim docstring) "Returns canonicalized `text` (lowercase and puncuation removed)": replaces `_` with space, strips `string.punctuation` (optionally keeping `{}`), lowercases, collapses whitespace.
- `prompt_engineering_constants.py`, verbatim:
  ```
  CLIP_BEST_PROMPT_TEMPLATES = [
      'itap of a {}.',
      'a bad photo of the {}.',
      'a origami {}.',
      'a photo of the large {}.',
      'a {} in a video game.',
      'art of the {}.',
      'a photo of the small {}.',
      '{}',
  ]
  ```
  `prompt_engineering_constants.py` defines only these two constants (`grep -n -E '^[A-Z_]+ = '`), so no SUN397/Places-specific templates exist in big_vision.
- Hugging Face `transformers` docs `model_doc/siglip.md` (verbatim): "To get the same results as the [`Pipeline`], a prompt template of `"This is a photo of {label}."` should be passed to the processor." and "make sure to pass `padding="max_length"` because that is how the model was trained." `model_doc/siglip2.md`: same template `f'This is a photo of {label}.'`, "IMPORTANT: we pass `padding=max_length` and `max_length=64` since the model was trained with this"; "Fixed padding and truncation: `padding="max_length"`, `max_length=64`, `truncation=True`"; "lowercasing and padding/truncation to length 64 are applied automatically by the processor pipeline."

---

## 5. Photo-genre label sets aimed at photographers

- **Photozilla** (Singhal et al., arXiv 2106.11359; PDF fetched, local `photozilla.txt`): "over 990k images belonging to 10 different photographic styles" collected from Flickr by tag ("For example, to crawl images in travel photography, 'travel' was used as the specific tag"), "Each class of 10 has approximately ~100k number of images", plus 10 additional classes with 75 images each for few-shot evaluation. **The 20 style names appear only inside Figures 2 and 3 (images), not in the text; the dataset site https://trisha025.github.io/Photozilla/ returns a GitHub Pages 404 and has no Wayback snapshot. Names: unverified.**
- **Flickr Style** (Karayev et al. 2014, arXiv 1311.3715, HTML fetched; verbatim from Sec. 3): "Optical techniques: Macro, Bokeh, Depth-of-Field, Long Exposure, HDR • Atmosphere: Hazy, Sunny • Mood: Serene, Melancholy, Ethereal • Composition styles: Minimal, Geometric, Detailed, Texture • Color: Pastel, Bright • Genre: Noir, Vintage, Romantic, Horror" (20 labels, 80,000 images). Style, not genre, taxonomy.
- **Unsplash Lite/Full dataset** (https://raw.githubusercontent.com/unsplash/datasets/master/DOCS.md, fetched): files are `photos.tsv`, `keywords.tsv` (one row per photo-keyword with AI-service confidences and `suggested_by_user`), `collections.tsv` (`collection_type` = "collection or topic"; "Topics are different content-specific photo feeds"), `conversions.tsv`, `colors.tsv`. **There is no genre/category label field**; the closest are free-text keywords and Unsplash "topic" titles.
- No other primary ML-literature label set for photographer-facing genres (portrait / landscape / street / wildlife ...) was found in this pass. **unverified / none found.**

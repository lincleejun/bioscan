# Category / label taxonomies in photographer-oriented and open-source photo managers

Researched 2026-09-24 from primary sources only (vendor docs, source code on GitHub, official API docs).
Anything not read directly from such a source is marked **unverified**. Factual only; no proposal.

Legend: *depth* = levels in the hierarchy; *labels/photo* = single vs multi-label.

---

## 1. PhotoPrism (open source)

**Source of the taxonomy:** `internal/ai/classify/rules.yml` (also generated into `rules.go`).
- https://github.com/photoprism/photoprism/blob/develop/internal/ai/classify/rules.yml
- raw: https://raw.githubusercontent.com/photoprism/photoprism/develop/internal/ai/classify/rules.yml
- last commit touching the file at time of reading: `746f26724c` 2025-10-02 "AI: Improve the generation, sorting, and filtering of labels #5232"
- `internal/ai/classify/label.go`, `internal/entity/label.go`, `internal/entity/category.go` (structure)
- User docs: https://docs.photoprism.app/user-guide/organize/labels/

**Mechanism (from the files):** each rule key is an ImageNet-style classifier output (e.g. `persian cat`, `aircraft carrier`).
A rule has `label` (curated name), `threshold`, `priority`, `categories` (list of broader labels), or `see: <other key>` (alias; `see: ignore` drops it).
`entity/category.go`: `Category` is a label-to-label join table ("links labels to a root label representing the shared meaning"); `entity/label.go` has `LabelCategories []*Label` (many2many `categories`). There is **no fixed enum of labels in `label*.go`**; label and category rows are created in the DB from the rules as photos are indexed. Labels carry `Uncertainty`, `Priority`, `Topicality`, `NSFW` (`classify/label.go`).

**Counts (computed from rules.yml, 5413 lines, alias-resolved):**
- 1218 rule keys; 559 are `see:` aliases; 138 resolve to `ignore`.
- **252 distinct curated labels** after alias resolution.
- **58 distinct category names.** 51 of them are also labels; a category can itself have categories (e.g. `cat` -> `animal`, `wild cat` -> `cat`, `beach` -> `water,sand`), so the structure is a **DAG of label-to-label links, not a fixed-depth tree** (effective depth 1 to ~3).
- Priorities used: 5 (143 rules), 4, 3, 2, 1, 0 (default), -1, -2, -3.
- Labels/photo: **multi-label** (one photo gets several labels, each with confidence; docs page confirms multiple labels per photo).

**All 58 categories** (N = rules whose own rule, or whose alias-resolved target rule, lists this category; sample of labels under it):
- animal (505): alligator, animal fur, ape, baboon, badger, bear, bee, beetle, bighorn, bird, buffalo, butterfly, cat, dog ...
- vehicle (60): aircraft, bike, boat, bus, cab, camper, car, horse cart, jeep, limousine, pickup, runway, scooter, ship, truck
- water (56): beach, boat, boathouse, crayfish, diving, dock, fish, jellyfish, lakeside, lobster, pier, seashore, shark
- people (53): baby, portrait (51 rule keys resolve to `portrait`, mostly clothing/uniform classes: abaya, academic gown, bikini, bow tie, cloak, fur coat, gown, groom, jersey, kimono, lab coat, military uniform, suit, wig ...)
- food (47): bagel, banana, cooking, dessert, french loaf, fruit, meat, pasta, pineapple, pizza, plate, pretzel, pumpkin, soup
- architecture (26): barn, beacon, boathouse, bridge, building, castle, church, dome, greenhouse, historic, pier, shelter, stairs, tower
- reptile (22): alligator, chameleon, crocodile, lizard, turtle
- wildlife (19): animal, baboon, bighorn, cheetah, elephant, gazelle, hartebeest, hippo, impala, leopard, warthog, wild cat, zebra
- dining (18): bowl, dessert, pasta, soup, vegetables
- beverage (12): bottle, coffee, cup, drinks, teapot, wine
- insect (11): butterfly, cockroach, dragonfly, grasshopper
- bird (10): chicken, duck, goose, hummingbird, ostrich, owl, peacock, penguin
- outdoor (10): barn, boathouse, camping, monument, park, shelter, viewpoint
- cat (9): cheetah, leopard, lion, puma, wild cat
- cooking (9): barbecue, bowl, lobster, meat, toaster, vegetables, wok
- farm (8): buffalo, cart, chicken, cow, field, hog, sheep, tractor
- fish (5): jellyfish, shark
- info (5): document, website
- nature (4): flower, plant, valley
- car (4): cab, jeep, pickup, vehicle
- kitchen (4): cooking, refrigerator, wok
- portrait (4): heritage, mask, sunglasses
- landscape (3): alpine, field, valley
- airport (3): aircraft, runway
- shop (3): bakery, butcher, store
- building (3): church, greenhouse, tower
- bakery (3): bagel, french loaf, pretzel
- plant (2): flower
- monkey (2): baboon, lemur
- furniture (2): bookcase, couch
- historic (2): castle, throne
- indoor (2): furniture
- computer (2): keyboard, monitor
- office (2): computer
- vegetables (2): pumpkin
- photography (2): camera, tripod
- drinks (2): bottle, wine
- fruit (2): banana, pineapple
- one rule each: wild cat (lion), beach (seashore), lobster (crayfish), beetle (dung beetle), tower (beacon), book (bookcase), weapon (cannon), store, music (microphone), electronics (kitchen), screen (monitor), cow (cart), snow (ski), train (streetcar), ship (submarine), mountain (alpine), sand (beach), baby (cradle), event (festival), church (altar)

**Full curated label list (252):** aircraft, alligator, alpine, altar, animal, animal fur, ape, architecture, baboon, baby, backpack, badger, bag, bagel, bakery, balloon, banana, barbecue, barn, basket, beach, beacon, bear, bee, beetle, bench, beverage, bighorn, bike, bird, boat, boathouse, book, bookcase, bottle, bowl, bridge, bucket, buffalo, building, bus, butcher, butterfly, cab, camera, camper, camping, candle, cannon, car, cart, castle, cat, centipede, chair, chameleon, cheetah, chicken, church, cinema, cockroach, coffee, comic, computer, cooking, couch, cow, crab, cradle, crayfish, crocodile, cup, dessert, dining, display, diving, dock, document, dog, dome, dragonfly, drinks, duck, dung beetle, echidna, electronics, elephant, fan, farm, festival, field, fish, flag, flower, fly, food, fox, french loaf, frog, fruit, furniture, gallery, gas station, gazelle, glass, goose, grasshopper, greenhouse, hartebeest, helmet, heritage, hippo, historic, hog, horse cart, hummingbird, impala, indoor, info, insect, instrument, jeep, jellyfish, keyboard, kitchen, koala, lakeside, lampshade, landscape, lemur, leopard, limousine, lion, living, lizard, llama, lobster, mailbox, mask, meat, meerkat, memorial, microphone, moment, monitor, monkey, monument, mountain, nature, office, ostrich, otter, outdoor, owl, panda, park, parking, pasta, peacock, penguin, people, photography, pickup, pier, piggy bank, pineapple, pizza, plant, plate, portrait, pretzel, public transport, puma, pumpkin, puzzle, rabbit, radio telescope, refrigerator, rocks, runway, safe, salamander, scooter, scorpion, screen, sea lion, seashore, shark, sheep, shell, shelter, ship, shoe, shop, sign, ski, skunk, snail, snow, snowmobile, soup, spider, spoon, stage, stained glass, stairs, starfish, store, street, streetcar, submarine, sunglasses, teapot, theater, theme park, throne, toaster, tool, tower, toy, tractor, traffic, train, tripod, truck, turtle, umbrella, valley, vase, vegetables, vehicle, viewpoint, wall, wallaby, warthog, water, weapon, weasel, website, whale, wild boar, wild cat, wildlife, window, wine, wing, wok, wolf, wombat, wood, worm, zebra.

Notes: the classifier is a TensorFlow SavedModel (`internal/ai/classify/model.go`, `type Model struct { model *tf.SavedModel ... }`); rule keys are ImageNet-1k-style class names. Which exact network (task prompt says NASNet) - **unverified**, not read from source. `rules.yml` is the mapping layer. No separate "top-level" list exists beyond the 58 category names above. NSFW label list (`internal/ai/nsfw`) not read (raw URL guessed returned 404) - **unverified**.

---

## 2. Immich (open source)

Sources:
- https://docs.immich.app/features/smart-search
- https://docs.immich.app/features/tags
- https://github.com/immich-app/immich/discussions/2660 (v1.60.0 release discussion)
- https://github.com/immich-app/immich/releases/tag/v1.91.0
- https://github.com/immich-app/immich/commit/092a23fd7fb80c72f2c693ffdc0fbc2d44f15caf ("feat(server,ml): remove image tagging (#5903)", 2023-12-21)
- https://github.com/immich-app/immich/blob/v1.90.0/machine-learning/app/models/image_classification.py and `server/src/domain/system-config/system-config.core.ts` at v1.90.0
- https://api.github.com/repos/immich-app/immich/contents/machine-learning/immich_ml/models (current model folders)
- https://huggingface.co/microsoft/resnet-50 (model card)

**Current state (docs + repo main):** **no automatic label taxonomy.** Machine-learning models folder contains only `clip`, `facial_recognition`, `ocr`. Smart search = CLIP embeddings ("contextual CLIP search powered by the VectorChord extension"), free-text; docs list selectable CLIP models (e.g. `ViT-B-16-SigLIP__webli`, multilingual `nllb`/`xlm` families). Search facets exposed in the docs: People (faces), Contextual (CLIP), file metadata, OCR text, Locations (reverse geocoding city/state/country), user tags/description/rating, camera make/model/lens, media type image/video. Tags (`features/tags`) are **user-created, hierarchical**, read from XMP `TagsList` / IPTC `Keywords`, written back to XMP sidecars; not assigned automatically.

**History:** v1.60.0 (2023-06): "Remove the object detection mechanism since the results are poor and not very useful at the moment" while "still keeping the Image Classification". v1.90.0 default classifier: `modelName: 'microsoft/resnet-50'`, `minScore: 0.9` (ImageNet-1k per the HF model card, 1000 classes) exposed as feature flag `TAG_IMAGE`. v1.91.0: "disable classification by default (#5708)". Commit 092a23fd (PR #5903, 2023-12-21): image tagging removed entirely.

Depth: n/a (no automatic taxonomy). Labels/photo: n/a; historic classifier was multi-label above threshold (top-k of ImageNet-1k, **exact k unverified**).

---

## 3. Desktop / photographer-oriented managers

### digiKam (open source) - Auto-Tags Assignment
Sources:
- https://docs.digikam.org/en/maintenance_tools/maintenance_autotags.html
- https://www.digikam.org/news/2024-03-17-8.3.0_release_announcement/ (and 8.2.0 announcement via search, https://www.digikam.org/news/2023-12-03-8.2.0_release_announcement/, not fetched directly)

Quoted: "The default model is EfficientNet B7 ... a general-purpose model that can detect 1,000 different objects and scenes." "Both YOLOv11 models [Nano, XLarge] are trained to detect 80 different objects based on the COCO dataset." "Tags generated by the Auto-Tags Assignment process will be under the **auto** tag in the Tags view." 8.3.0 announcement: "detect forms, objects, places, animals, plants, monuments, scenes, and more"; OpenCV DNN engine.
- Taxonomy: raw model class names, i.e. 1000 classes (docs say "1,000 objects and scenes" but **never say ImageNet**; ImageNet-1k is implied by the count only - **unverified**) or 80 COCO classes. No curated category grouping.
- Depth: 2 (`auto` parent tag -> class tag). Labels/photo: multi-label; "accuracy" slider (default 7) controls how many.
- Class list files in source: GitHub mirror tree (`KDE/digikam`, master, recursive listing) contains no file matching `autotag*` + class/label/coco/imagenet - **class list not located in source**.

### Excire Foto
Sources:
- https://excire.com/manuals/ExcireFoto2024_Quickstart-EN.pdf (read; text extracted)
- https://excire.com/en/photo-tagging/, https://excire.com/en/tutorials/excire-foto-2024/tagging/ (no list)
- https://learning-center.excire.com/... (page body did not load)

Quoted from the Quickstart: "Below that you will find the keyword hierarchies of the keywords assigned by Excire Foto." "Keywords that are already included in the AI keyword hierarchy of Excire Foto are displayed with a blue frame ... Behind keywords assigned by the AI, a number is displayed (0.01 - 0.99) indicating which certainty ... the AI assumes." "In the Excire Foto keyword hierarchy, only the keywords that have already been assigned to one or more photos during the analysis are displayed. It is not a complete list of all keywords available in Excire Foto. The Excire Foto AI keyword hierarchy cannot be modified by the user."
- Taxonomy: fixed, hierarchical, proprietary; **the category list is not published in the manual or on the pages fetched** (task premise "they publish their keyword categories" - **not confirmed**; a site-restricted search for a keyword list on excire.com / support.excire.com / learning-center.excire.com found only the Quickstart PDFs and the same "not a complete list" statement). Only the animal > bird / mammal > bear, wolf, fox example appears in marketing copy (search snippet, **unverified**).
- Depth: >= 3 per that example. Labels/photo: multi-label with per-keyword probability.

### Peakto (CYME)
Source: https://cyme.io/en/peakto/features/photo-search/
Quoted automatic subject categories: **Architecture, Astro-photography, Wildlife, Automotive, Events & Wedding, Fashion, Food & Drinks, Nature, People, Portrait, Screenshots, Sport, Street, Water & Underwater** (14).
Aesthetic styles: Abstract, Aerial, Close Up, Night. Colour harmony: Compound, Complementary, Triad, Simple Split. Named colours (Brown, Chocolate, Goldenrod, Yellowgreen, Aquamarine, ...). People grouping: Alone, InDuo, Small Group, Large Group. Lighting: Bright, Dark, Highly Saturated, Undersaturated, High Contrast, Low Contrast.
- Depth: flat lists per dimension (subject / style / colour / people / lighting). Labels/photo: several dimensions per photo; whether >1 subject category per photo is possible is **not stated**.

### Mylio Photos
Sources:
- https://manual.mylio.com/topic/working-with-categories
- https://manual.mylio.com/24.3/en/topic/using-smarttags
- https://manual.mylio.com/24.3/en/topic/comprehensive-guide-to-quickfilters

**Correction to the task premise:** "People, Places, Documents, Screenshots" are not Mylio *Categories*.
- **Categories** (manual, not AI): five built-in - **Personal, Family, Family History, Work, Private** - plus "40+ additional custom Categories". Stored only in the Mylio library (not XMP). Flat. Applied to media, folders, albums, people, calendar events.
- **SmartTags** (automatic, local computer vision): "recognize objects and other visual traits ... exposure, focus, color, and specific types of animals, buildings, vehicles, and more". **The tag list is not published**; count "over 1,000" appears only in marketing/search snippets (**unverified**). Multi-label. Can be promoted to Keywords.
- **QuickFilters "By File"**: type = **Photos, Videos, Documents, Screenshots** (auto-detected file kinds). Other filter groups: Ratings/Labels/Flags, SmartTag, Keyword, Folder, Date (incl. time-of-day buckets "Early Morning" 03:00-05:59 ... "Night" 22:00-02:59), Event, Album, Category, Person, Visual Property, Camera & Lenses, Custom.

### Aftershoot (culling)
Sources:
- https://support.aftershoot.com/en/articles/10570203-aftershoot-culling-genres
- https://support.aftershoot.com/en/articles/5223473-get-started-with-aftershoot-culling

Culling **genres** (user picks one per job): Weddings & Engagements, Portrait & Headshots, Family Portraits, Boudoir, Sports, School Portraits, School Events, New Born, Something Else (9).
Output buckets - the two pages name them differently:
- genres page: Selected, Highlights, Blur, Closed Eyes, Duplicates, Warning.
- get-started page: "AI Selections" = Selected, Highlights or Maybe, Duplicates; "For Review" = Blurred, Closed Eyes; plus My Selections, Unrated.
- Depth: 2 (bucket group -> bucket). Labels/photo: one bucket per photo (plus duplicate grouping); it's a quality/culling taxonomy, not a subject taxonomy.

### Narrative Select (culling)
Source: https://narrative.so/select
Named assessments: Eye assessment (eyes open/closed), Focus assessment (per face), Image Assessment ("identify the worst images"), Face Assessments, Close-ups, Scenes (grouping, ranked by sharpness), People Filter, Key Element Detection. Colour-coded status (green / yellow / orange-red) per search snippet (**unverified** wording). No subject taxonomy. Multi-assessment per photo.

### FilterPixel (culling)
Source: https://filterpixel.com/culling
Buckets on that page: **Keepers, Review, Rejects**; detected issues: "Blur & Focus Check", "Accidental Blink Detection", "Emotion Recognition". The blog (https://filterpixel.com/blog/posts/how-to-use-filterpixel/, via search, **unverified**) names them Selected / Rejected / Duplicate. One bucket per photo. No subject taxonomy.

---

## 4. Adobe Lightroom (cloud) - Sensei auto-tagging
Source URL: https://helpx.adobe.com/lightroom-cc/how-to/search-find-photos-ratings-flags-lightroom-cc.html (also /in/, /ee/ locales) - **every fetch returned HTTP 403**; content below is from search-engine snippets of that page and is **unverified**.
Snippet: "Sensei ... can analyze your photos and auto tag them based on their content ... enter an item you think appears in some of your photos, like 'water' or 'food'." Search happens in the cloud.
- **No published category list found.** Tags are free-form searchable terms; no hierarchy stated. Multi-label implied.
- Lightroom Classic: whether it has any Sensei auto-tagging is **unverified** (https://helpx.adobe.com/lightroom-classic/help/keywords.html also returned 403). "Lightroom Downloader" - nothing primary found; skipped.

---

## 5. Photography genre taxonomies aimed at photographers

### 500px
Source: https://github.com/500px/api-documentation/blob/master/basics/formats_and_terms.md (raw fetched; repo last pushed 2019-07-30, not archived).
Categories (ID): Uncategorized (0), Abstract (10), Aerial (29), Animals (11), Black and White (5), Celebrities (1), City and Architecture (9), Commercial (15), Concert (16), Family (20), Fashion (14), Film (2), Fine Art (24), Food (23), Journalism (3), Landscapes (8), Macro (12), Nature (18), Night (30), Nude (4), People (7), Performing Arts (19), Sport (17), Still Life (6), Street (21), Transportation (26), Travel (13), Underwater (22), Urban Exploration (27), Wedding (25). = **30 incl. Uncategorized**; the task's list of 27 lacks Aerial, Night, Uncategorized; doc spells "City and Architecture".
- Depth: 1 (flat). Labels/photo: **single** - the photo object has one `"category": <int>` field ("A numerical ID for the Category of the photo", https://github.com/500px/api-documentation/blob/master/endpoints/photo/GET_photos_id.md and POST_photos.md). Whether the current 500px site still uses this list: **unverified**.

### Unsplash Topics
Source: https://unsplash.com/documentation (HTML fetched; Topics section quoted).
`GET /topics` - "Get a single page from the list of all topics." params `ids` (ids or slugs), `page`, `per_page`, `order_by` (featured, latest, oldest, position). Topic object fields: id, slug, title, description, published_at, updated_at, starts_at, ends_at, only_submissions_after, visibility ("featured"), featured, total_photos, links, status ("open"), owners... Example topic: `wallpapers` ("From epic drone shots to inspiring moments in nature ..."). Also `GET /topics/:id_or_slug`, `GET /topics/:id_or_slug/photos`.
- Topics are **editorially curated, dynamic (start/end dates, submissions), not a fixed taxonomy**; the docs list no fixed slugs. Current docs have **no `categories` endpoints**. The live topic list (unsplash.com/t, /napi/topics) was behind a bot wall - **not retrieved**. Photos-per-topic cardinality (whether one photo can sit in several topics) was not confirmed from the photo object schema - **unverified**.

### Flickr
Source: https://www.flickr.com/services/api/flickr.photos.setContentType.html
`content_type`: "1 for Photo, 2 for Screenshot, 3 for Other, 4 for Virtual Photography". Single value per photo; that is the only fixed content taxonomy in the API (no genre categories).

### Shutterstock
Source: https://api-reference.shutterstock.com/ (`GET /v2/images/categories`, returns `{id, name}` list, `language` param). The page's only example is Spanish: Abstractos, Animales/ Naturaleza, Las Artes, Fondos/Texturas, Belleza/Moda, Edificios/Lugares Famosos, Negocios/Finanzas, Educación (truncated example).
English list (Abstract, Animals/Wildlife, The Arts, Backgrounds/Textures, Beauty/Fashion, Buildings/Landmarks, Business/Finance, Celebrities, Editorial, Education, Food and Drink, Healthcare/Medical, Holidays, Industrial, Interiors, Miscellaneous, Nature, Objects, Parks/Outdoor, People, Religion, Science, Signs/Symbols, Sports/Recreation, Technology, Transportation, Vintage ...) comes from search snippets of https://www.shutterstock.com/explore/royalty-free-images (fetch returned 403) - **unverified**. Flat; contributors pick up to two categories per image (**unverified**).

### Getty Images
Not covered (no primary page fetched).

---

## Cross-product summary table

| Product | Auto taxonomy? | Size | Depth | Labels/photo | Published list? |
|---|---|---|---|---|---|
| PhotoPrism | yes, curated over ImageNet-style classes | 252 labels / 58 categories | DAG, ~1-3 | multi | yes (rules.yml) |
| Immich | no (CLIP embeddings; user tags only); ImageNet-1k ResNet-50 tagging removed 2023-12 | - | - | - | n/a |
| digiKam | raw model classes under `auto` tag | 1000 (EfficientNet B7) / 80 COCO (YOLOv11) | 2 | multi | no curated list; class files not located |
| Excire Foto | proprietary keyword hierarchy | not stated | >=3 (example) | multi, with probability | no |
| Peakto | fixed subject + style + colour + people + lighting | 14 subjects, 4 styles, ... | flat per dimension | multi-dimension | yes (feature page) |
| Mylio | SmartTags (auto, unpublished); Categories are manual (5 built-in) | - | flat | multi | no (SmartTags) / yes (Categories) |
| Aftershoot | culling buckets + 9 shoot genres | 5-8 buckets | 2 | single bucket | yes |
| Narrative Select | per-face/image assessments | - | - | multi | yes (names only) |
| FilterPixel | culling buckets | 3 | 1 | single | yes (names vary by page) |
| Lightroom cloud | Sensei free-form tags | not published | - | multi | no (page 403; unverified) |
| 500px | photo genre category | 30 | 1 | single | yes (API doc, 2019) |
| Unsplash | curated Topics, dynamic | variable | 1 | multi | no fixed list |
| Flickr | content type | 4 | 1 | single | yes |
| Shutterstock | stock categories | ~27 | 1 | up to 2 (unverified) | API endpoint; English names unverified |

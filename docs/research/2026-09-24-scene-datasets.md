# Open-licence photo datasets for a scene/genre ground-truth set — survey (2026-09-25)

Scope: 8 groups / 40 labels + 3 attributes (light, setting, framing). Facts only; no proposal.
Every fact carries its source URL. "unverified" = not confirmed from a primary source during this run.
Counts marked *computed* were produced by downloading the official CSVs / calling the official API today; the scripts and raw outputs sit next to this file (`oid_intersections.txt`, `oid_counts.txt`, `commons_counts.txt`, `commons_recursive_part*.txt`).

Licence buckets used below: CC0/PD · CC BY · CC BY-SA (share-alike, listed separately because it is not in the task's list) · CC BY-NC · research-only (non-commercial, no redistribution) · platform licence (Unsplash/Pexels/Pixabay) · unknown.

---

## 1. Places365 (Places2, MIT CSAIL)

| Field | Value | Source |
|---|---|---|
| URL | http://places2.csail.mit.edu/download.html (plain HTTP answers; HTTPS refused connection today) | http://places2.csail.mit.edu/download.html |
| Size | 365 scene classes; train 1,803,460 / **validation 36,500 (= 100/class)** / test 328,500 per TFDS. The MIT download page text says "There are 50 images per category in the validation set and 900 images per category in the testing set" — I treat the page text as stale; the val tarball counted by TFDS is 36,500. | https://www.tensorflow.org/datasets/catalog/places365_small ; http://places2.csail.mit.edu/download-private.html |
| Labels | 365 scene categories (list: `categories_places365.txt`); `IO_places365.txt` marks each class indoor (1) / outdoor (2): **161 indoor, 204 outdoor** (computed). No light, framing, subject or genre labels. | https://raw.githubusercontent.com/CSAILVision/places365/master/categories_places365.txt ; https://raw.githubusercontent.com/CSAILVision/places365/master/IO_places365.txt |
| Mapping to our labels (val = 100/class) | landscape: mountain ≈ 9 cats (mountain, mountain_path, mountain_snowy, butte, cliff, valley, volcano, canyon, rock_arch) → ~900; coast ≈ 9 (coast, beach, ocean, wave, lagoon, islet, harbor, pier, lighthouse) → ~900; freshwater ≈ 13 (lake/natural, river, creek, pond, waterfall, swamp, marsh, hot_spring, swimming_hole, canal/natural, fishpond, moat/water, watering_hole) → ~1,300; forest 6 (forest/broadleaf, forest_path, forest_road, rainforest, bamboo_forest, tree_farm) → 600; grassland ≈ 7 (field/wild, field/cultivated, pasture, hayfield, wheat_field, corn_field, tundra) → 700; desert 4 (desert/sand, desert/vegetation, desert_road, badlands) → 400; snow_ice ≈ 10 (snowfield, glacier, ice_floe, ice_shelf, iceberg, igloo, crevasse, ski_slope, ski_resort, mountain_snowy) → ~1,000; sky 1 → 100. architecture: interior = the 161 indoor classes → 16,100; building ≈ 40 outdoor-building classes → ~4,000; cityscape ≈ 15 (downtown, street, alley, crosswalk, plaza, residential_neighborhood, medina, slum, industrial_area, skyscraper, promenade, canal/urban, highway, bridge, viaduct) → ~1,500; monument ≈ 10 (mausoleum, ruin, arch, fountain, cemetery, archaelogical_excavation, burial_chamber, catacomb, amphitheater, tower) → ~1,000; rural ≈ 14 (farm, barn, corral, pasture, village, orchard, rice_paddy, vineyard, hayfield, field_road, windmill, cottage, stable, kennel/outdoor) → ~1,400. setting: indoor/outdoor from IO file; underwater 1 class (underwater/ocean_deep) → 100. **Not covered**: all wildlife, all night (no night/astro/aurora/moon class), all people (sport only as empty venues: stadium/*, football_field, …), all macro, food_drink (only food *venues*: bakery/shop, restaurant, …), vehicle (only car_interior/bus_interior/cockpit/airfield…), still_life, art (art_gallery/art_studio are rooms), utility, abstract, light attribute, framing attribute. Category→label mapping is mine (unverified by anyone else). | categories file above |
| Image licence | Terms of use (verbatim): "by downloading the image data you agree to the following terms: You will use the data only for non-commercial research and educational purposes. You will NOT distribute the above images. Massachusetts Institute of Technology makes no representations or warranties regarding the data …". GitHub README: "The copyright of all the images belongs to the image owners." → **research-only, no redistribution**. Pretrained CNNs are CC BY. | http://places2.csail.mit.edu/download-private.html ; https://github.com/CSAILVision/places365 |
| Redistribution / download-at-test-time | Redistribution prohibited. Download at test time is HTTP tar from MIT (val_256: 501 MB, MD5 e27b17d8…; val large: 2.1 GB). No per-image URL list is published for val, so re-fetching from origin is not an option. | http://places2.csail.mit.edu/download-private.html |
| Per-image licence field | none | — |
| Download mechanics | HTTP tar (see above); also TFDS `places365_small` (29.27 GiB, includes train). | TFDS page |

## 2. SUN397 (Princeton / MIT)

| Field | Value | Source |
|---|---|---|
| URL | https://vision.princeton.edu/projects/2010/SUN/ (redirects to a 404 today; read via Wayback 2025-06-19) | http://web.archive.org/web/20250619154319/https://vision.princeton.edu/projects/2010/SUN/ |
| Size | 397 categories, ≥100 images/category, **108,754 images**; official benchmark partitions: 50 train + 50 test per class × 10 partitions. | same |
| Labels | 397 scene categories (list from TFDS `sun397_labels.txt`). Indoor/outdoor is in the SUN hierarchy (unverified here). No light/framing/subject labels. | https://raw.githubusercontent.com/tensorflow/datasets/master/tensorflow_datasets/datasets/sun397/sun397_labels.txt |
| Mapping to our labels | Similar to Places365 with extras: forest/needleleaf, waterfall/block\|fan\|plunge, sea_cliff, bayou, sandbar, hill, underwater/coral_reef. Same gaps: no wildlife, night, people-as-subject, macro, food, vehicle-as-subject, art, utility, abstract, light, framing. Per-label counts ≈ (mapped categories) × (≥100), e.g. mountain 11 cats ≥1,100; freshwater 15 cats ≥1,500; forest 8 ≥800; desert 3 ≥300; snow_ice ~10 ≥1,000; sky 1 ≥100; underwater 1 ≥100. | labels file |
| Image licence | "The images provided here are for research purposes only." Image origin (search engines) stated in the paper, not on the page → unverified. | Wayback page |
| Redistribution / download | Tar (SUN397.tar 37 GB / .tar.gz 39 GB) from Princeton; a "Dataset Image URLs" list is also linked (not fetched → contents unverified). HF mirrors (e.g. `tanganke/sun397`) repeat "for research purposes only". | Wayback page ; https://huggingface.co/datasets/tanganke/sun397 |
| Per-image licence field | none | — |

## 3. iNaturalist Open Data (AWS) + iNaturalist API

| Field | Value | Source |
|---|---|---|
| URL | https://registry.opendata.aws/inaturalist-open-data/ ; docs https://github.com/inaturalist/inaturalist-open-data | both |
| Size | "over 400 million photos" in bucket `inaturalist-open-data`; metadata CSVs regenerated monthly; photos copied in near-real time. | GitHub README ; AWS registry |
| Files / columns | `photos.csv`: photo_uuid, photo_id, observation_uuid, observer_id, extension, **license**, width, height, position. `observations.csv`: observation_uuid, observer_id, latitude, longitude, positional_accuracy, taxon_id, quality_grade, observed_on, anomaly_score. `taxa.csv`: taxon_id, ancestry, rank_level, rank, name, active. `observers.csv`: observer_id, login, name. Photo URL: `https://inaturalist-open-data.s3.amazonaws.com/photos/{photo_id}/{size}.{ext}`, sizes original(2048)/large(1024)/medium(500)/small/thumb/square. | https://github.com/inaturalist/inaturalist-open-data/tree/main/Metadata |
| Licences included | Metadata README: "All photos in the dataset have open licenses (e.g. Creative Commons) and unlicensed (CC0 / public domain)". Attribution guidance names CC0, CC-BY **and CC-BY-NC** examples → the bucket includes NC photos; filter on `license`. API enumerates the licence vocabulary: `cc-by, cc-by-nc, cc-by-nd, cc-by-sa, cc-by-nc-nd, cc-by-nc-sa, cc0`. API docs: "Photos in the `inaturalist-open-data` domain are shared under open licenses … Photos in the `static.inaturalist.org` domain do not have open licenses." | Metadata README ; https://api.inaturalist.org/v1/swagger.json |
| Per-image licence field | **yes** (`license` column; API `photos[].license_code`). | same |
| Redistribution / download | CC0 and CC BY photos: redistributable with attribution per licence. Bulk: S3 (no key, `--no-sign-request`). API: `photo_license=cc0,cc-by` filter; throttled "to a max of 100 requests per minute … keep under 10,000 requests per day", "The API is meant to support application development, not data scraping." | swagger.json info text |
| Scene/framing labels | **none**. Controlled terms (annotations) available today: Alive or Dead (18 Alive/19 Dead/20 CBD); Established (34); Life Stage (2 Adult, 3 Teneral, 4 Pupa, 5 Nymph, 6 Larva, 7 Egg, 8 Juvenile, 16 Subimago); Leaves (37–40); Evidence of Presence (23 Feather, 24 Organism, 25 Scat, 29 Gall, 26 Track, 27 Bone, 28 Molt, 30 Egg, 31 Hair, 32 Leafmine, 35 Construction); Flowers and Fruits (13 Flowers, 14 Fruits or Seeds, 15 Flower Buds, 21 None); Sex (10/11/20). Nothing for flight, herd/flock, portrait vs habitat, light, framing. `captive=true` gives captive/cultivated (→ domestic proxy). | https://api.inaturalist.org/v1/controlled_terms |
| Counts (computed today; **research-grade observations having ≥1 CC0/CC-BY photo**, not photo counts) | Aves 4,565,703 · Mammalia 549,441 · Insecta 5,913,759 · Arachnida 457,102 · Reptilia 457,225 · Amphibia 270,721 · Actinopterygii 306,329 · Fungi 725,145 · Plantae 9,858,812 · Plantae with annotation Flowers (term 12 / value 13) 1,114,619 · captive Aves+Mammalia 45,995 · captive all taxa 665,851 (these two: `captive=true` only, all quality grades) · all taxa 23,963,478. | `GET /v1/observations?taxon_id=…&photo_license=cc0,cc-by&quality_grade=research&per_page=1` |
| Coverage | bird_portrait/bird_habitat/bird_flight only as an undifferentiated "bird" pool; same for mammal_*; other_animal; insect_macro (taxon only, framing unknown); fungi; flower (Flowers annotation); plant; domestic (captive proxy). No landscape/night/people/architecture/food/other, no attributes. | — |

## 4. iNaturalist 2021 competition dataset (visipedia/inat_comp)

| Field | Value | Source |
|---|---|---|
| URL | https://github.com/visipedia/inat_comp/tree/master/2021 | same |
| Size | 10,000 species; train ~2.7 M; train-mini 500 K (50/species); **val = 10 images/species = 100,000**; test 500,000. Tarballs on `s3://ml-inat-competition-datasets/2021/` (val.tar.gz 8.4 GB). | README |
| Labels | species + `license` (int) and `rights_holder` per image (COCO-style JSON), lat/lon/date. No scene/framing labels. | README |
| Licence | Terms of Use: "You will use the data only for non-commercial research and educational purposes." "You will NOT distribute the dataset images." Underlying photos are iNat CC photos (mix incl. NC). → **research-only as a package**; per-image licence field exists but the package terms still forbid redistribution. | README |
| Coverage | same taxon-only coverage as §3, but with a fixed, balanced split. | — |

## 5. Unsplash Lite dataset + Unsplash API

| Field | Value | Source |
|---|---|---|
| URL | https://github.com/unsplash/datasets ; terms https://github.com/unsplash/datasets/blob/master/TERMS.md ; API https://unsplash.com/documentation | all |
| Size | Lite: 25,000 photos, ~30,000 keywords, 1 M searches (~700 MB compressed TSV). Full: 5 M+ (request access, non-commercial). Image files are **not** shipped; `photos.tsv` carries `photo_image_url`. | README ; DOCS.md |
| Labels | `photos`: photo_description, ai_description, ai_primary_landmark_*, exif_*, photo_location_*, blur_hash… `keywords`: keyword, ai_service_1/2_confidence, suggested_by_user, ai_service_3 fields. `collections`: collection_title. `colors`. Keywords are AI/user tags, not curated genre labels. | DOCS.md |
| Licence — three texts, quoted side by side, not reconciled | (a) Lite dataset TERMS.md: licence "to download and store any photos, images, or other data contained in the Lite Dataset … and internally use the Commercial Licensed Data to train machine learning models or algorithms for your internal business purposes"; prohibits "disclose, deliver, disseminate, or publish any portion of the Licensed Data in any manner", "sublicense, resell, relicense or redistribute", and "publish or publicly disclose the results of any comparison of the Datasets or Licensed Data to similar datasets". README: the dataset "cannot be used to redistribute the images". (b) unsplash.com/terms §8 (Prohibited Conduct): "Use the Images in connection with any machine learning and/or artificial intelligence datasets (e.g., training any machine learning and/or artificial intelligence models), or for technologies designed or intended for the identification of natural persons" — points ML users to unsplash.com/data; also bans "bots, spiders, scripts, crawlers, scrapers" except the API. (c) API terms §12: "In the event you desire to use the Content sourced from the API in connection with any machine learning and/or artificial intelligence purposes … please visit https://unsplash.com/data"; §6 requires hotlinking the API-returned image URLs. Unsplash License itself: free incl. commercial, no attribution; bans selling unaltered copies and "Compiling images from Unsplash to replicate a similar or competing service". | TERMS.md ; https://unsplash.com/terms ; https://unsplash.com/api-terms ; https://unsplash.com/license |
| Redistribution / download | Lite: images downloadable via the TSV URLs and storable, ML use permitted internally; **no redistribution, no publishing of dataset comparisons**. API: hotlink-only, demo 50 req/h; "After approval for production, this limit is increased to 1000 requests per hour", `/search/photos?query=&orientation=&color=`; image-file requests are not rate-limited. | documentation |
| Per-image licence field | no (all Unsplash License) | — |
| Per-label counts | unverified — requires downloading the Lite TSV and counting `keywords` rows (not done). | — |

## 6. Pexels API

| Field | Value | Source |
|---|---|---|
| URL | https://www.pexels.com/api/documentation/ ; licence https://www.pexels.com/license/ ; ToS https://www.pexels.com/terms-of-service/ | all |
| Labels | keyword search only (`query`, `orientation`, `size`, `color`, `locale`). No genre taxonomy. | API docs |
| Licence | Pexels License: "All photos and videos on Pexels are free to use", attribution optional; no selling unaltered copies; no redistribution on competing stock platforms. No AI/ML clause in the licence text. | licence page |
| ML / bulk | ToS §8: "Data mining, extraction, scraping and the use of programs or robots for automatic data collection and/or extraction of digital data on the Service and/or the content available therein is strictly prohibited for all unauthorised purposes, including without limitation for machine learning purposes." "Bulk, large-scale or systematic copying of Content is strictly prohibited unless explicit permission has been granted by us." | ToS |
| Download mechanics | API key; 200 req/h, 20,000/month; "show a prominent link to Pexels"; "may not copy or replicate core functionality of Pexels". No provision on offline storage. | API docs |
| Per-image licence field | no | — |
| Counts | unverified (no key used). | — |

## 7. Pixabay API

| Field | Value | Source |
|---|---|---|
| URL | https://pixabay.com/api/docs/ ; licence https://pixabay.com/service/license-summary/ ; ToS https://pixabay.com/service/terms/ | all |
| Labels | `q`, `image_type` (photo/illustration/vector), `orientation`, `category` ∈ {backgrounds, fashion, nature, science, education, feelings, health, people, religion, places, animals, industry, computer, food, sports, transportation, travel, buildings, business, music}, `editors_choice`, `colors`. | API docs |
| Licence | Content License: free, no attribution, modify allowed; no standalone sale/distribution; no trademark use. No AI clause in the summary. | licence summary |
| ML / bulk | API docs: "requests must be cached for 24 hours"; "do not send lots of automated queries. Systematic mass downloads are not allowed"; "permanent hotlinking of images … is not allowed". ToS §8: data mining/scraping "strictly prohibited for all unauthorised purposes, including without limitation for machine learning purposes"; "Bulk, large-scale or systematic copying of Content is strictly prohibited unless explicit permission has been granted by us." | API docs ; ToS |
| Download mechanics | API key; 100 req / 60 s. | API docs |
| Counts | unverified. | — |

## 8. Wikimedia Commons (MediaWiki API)

| Field | Value | Source |
|---|---|---|
| URL | https://commons.wikimedia.org/w/api.php ; policy https://commons.wikimedia.org/wiki/Commons:Licensing | both |
| Licence policy | Only free content: CC0, CC BY, CC BY-SA, PD, FAL, etc. "Commercial use of the work must be allowed", "Publication of derivative work must be allowed"; NC and ND variants rejected. "All description pages on Commons must indicate clearly under which license the materials were published." Reusers must check each file: "the Wikimedia Foundation does not provide any warranty regarding the copyright status". | Commons:Licensing ; Commons:Reusing_content_outside_Wikimedia |
| Per-file licence field | **yes** — `prop=imageinfo&iiprop=extmetadata` returns `LicenseShortName`, `License`, `UsageTerms`, `Artist`, `Credit` (verified today on Category:Birds_in_flight: e.g. `LicenseShortName: CC BY 4.0`, `License: cc-by-4.0`). | https://www.mediawiki.org/wiki/API:Imageinfo ; test call |
| Download mechanics | `list=categorymembers` / `generator=categorymembers&gcmtype=file&gcmlimit=500` (+ recursion over subcats), original file URL via `iiprop=url`; no key. Robot policy: identify User-Agent; Action API unauthenticated "keep the concurrency of your requests to 1 at a time, and below 5 requests per second overall"; media (upload.wikimedia.org) "total concurrency of at most 2, and limit your total download speed to 25 Mbps", prefer thumbnails. Anonymous requests without a UA were rate-limited/empty today. | https://wikitech.wikimedia.org/wiki/Robot_policy |
| Licence mix (computed, first 50 files of the root category) | Aurora borealis: CC0 3, PD 10, CC BY 20, CC BY-SA 17 · Aurora australis: PD 35, CC0 2, CC BY 6, BY-SA 6, "No restrictions" 1 · Birds in flight: CC0 2, PD 5, "No restrictions" 4, CC BY 10, **CC BY-SA 29** · Flocks of birds in flight: CC0 5, PD 10, CC BY 8, BY-SA 26, other 1. → roughly ⅓–⅔ share-alike depending on category; CC0/PD/CC BY share ~40–60 %. | `commons_recursive_part1.txt` |
| Category file counts (computed; root category only, `prop=categoryinfo`, files / subcats) | Aurora borealis 95/4 · Aurora australis 63/2 · Birds in flight 471/25 · Flocks of birds in flight 374/14 · Bird flight 44/7 · Milky Way Galaxy 1,112/27 · Night sky 886/13 · Star trails 197/3 · Noctilucent clouds 183/6 · Full moon 1,357/19 · Moon 811/36 · Light pollution 213/14 · Night photography 1,446/8 · Cities at night 35/8 · Night 1,640/41 · Blue hour 87/7 · Golden hour 0/0 · Sunsets 999/33 · Sunrises 807/31 · Macro photographs 1,587/12 · Macro photography 159/5 · Close-up photographs 547/9 · Underwater photographs 285/11 · Aerial photographs 55/17 · Aerial photography 108/22 · Street photography 1,090/11 · Portraits 14,047/55 · Portrait photographs 4,409/45 · Group portraits 1,442/18 · Group photographs 2,884/25 · Selfies 1,261/36 · Crowds 1,442/46 · Sports 2,513/80 · Sports photography 10/6 · Concerts 3,548/47 · Festivals 3,017/49 · Weddings 693/36 · Events 929/45 · Herds 147/17 · Flocks of sheep 249/4 · Livestock 156/40 · Pets 336/32 · Dogs 500/63 · Cats 138/33 · Wildlife photography 451/5 · Mammals 38/35 · Insects 444/81 · Butterflies 84/25 · Fungi 1,091/67 · Flowers 6/75 · Mountains 515/55 · Coasts 482/40 · Beaches 2,403/71 · Forests 5,630/56 · Grasslands 464/27 · Meadows 659/17 · Deserts 918/30 · Dunes 873/44 · Glaciers 187/61 · Ice 1,074/25 · Snow 0/86 · Lakes 994/72 · Rivers 1,277/80 · Waterfalls 1,228/39 · Ponds 1,503/34 · Wetlands 720/49 · Sky 2,593/15 · Clouds 0/51 · Landscape photography 918/19 · Landscapes 693/19 · Buildings 2,434/101 · Architecture 1,816/28 · Interiors 1,264/37 · Cityscapes 416/24 · Monuments 117/12 · Villages 259/37 · Rural areas 1/0 · Food photography 776/6 · Food 101/91 · Drinks 0/0 · Vehicles 7/145 · Paintings 5,255/72 · Abstract art 1,600/15 · Textures 913/55 · Screenshots 12,062/48 · Screenshots of software 577/18 · Documents 1,485/39. | `commons_counts.txt` |
| Recursive counts (computed, distinct files incl. subcategories) | Depth 2: Aurora borealis **1,628** (44 cats) · Aurora australis **303** · Birds in flight **5,717** (149 cats) · Flocks of birds in flight **2,517**. Depth 1 for 28 further categories (Milky Way 2,315, Night sky 1,607, Underwater 4,957, Sunsets 7,562, Screenshots 50,087, …): full table with licence samples in the addendum at the end of this file. | `commons_recursive_part1.txt` |
| Caveats | Category membership is human-curated but not exhaustive or exclusive (e.g. "Snow" has 0 direct files, 86 subcats); many files are scans/illustrations, not photos; licence must be read per file; BY-SA is share-alike. | — |

## 9. Flickr API (licence filter)

| Field | Value | Source |
|---|---|---|
| URL | https://www.flickr.com/services/api/flickr.photos.search.html ; licences https://www.flickr.com/services/api/flickr.photos.licenses.getInfo.html ; ToS https://www.flickr.com/services/api/tos/ | all |
| Licence codes | 0 All Rights Reserved · 1 CC BY-NC-SA 2.0 · 2 CC BY-NC 2.0 · 3 CC BY-NC-ND 2.0 · **4 CC BY 2.0** · 5 CC BY-SA 2.0 · 6 CC BY-ND 2.0 · 7 No known copyright restrictions · 8 US Government Work · **9 CC0** · **10 Public Domain Mark** · **11 CC BY 4.0** · 12 CC BY-SA 4.0 · 13 CC BY-ND 4.0 · 14 CC BY-NC 4.0 · 15 CC BY-NC-SA 4.0 · 16 CC BY-NC-ND 4.0. | licenses.getInfo |
| Search | `text`, `tags`, `group_id`, `license=4,9,10,11`; `per_page` ≤ 500; "Flickr will return at most the first 4,000 results for any given search query." | photos.search |
| Per-image licence field | yes (`license` in search response) | photos.search |
| API ToS vs photo licence (both quoted, not reconciled) | ToS: must not "Cache or store any Flickr user photos other than for reasonable periods in order to provide the service"; if a photo goes private "you must remove as soon as reasonably possible"; commercial API key required where "the primary purpose of your application is to derive revenue"; must display "This product uses the Flickr API but is not endorsed or certified by SmugMug, Inc." No ML clause. The CC licence on the photo itself permits copying/redistribution under its terms. | API ToS |
| Counts | unverified (needs API key; not run). Requires an API key. | — |

## 10. Flickr Style (Karayev et al.) and AVA (DPChallenge)

| Field | Value | Source |
|---|---|---|
| Flickr Style | 80,000 Flickr photographs, 20 curated style labels (Macro, Bokeh, Hazy, Sunny, Minimal, Geometric, Noir, …); Wikipaintings 85 K paintings / 25 styles. Distributed as Flickr URL lists (project page http://sergeykarayev.com/recognizing-image-style/ returned 404 today; vislab README fetch returned nothing) → **distribution format and licence filter unverified**; photos are whatever licence the Flickr group members chose (no CC filter stated in the paper abstract). | https://arxiv.org/abs/1311.3715 |
| AVA | "250000+ photos from dpchallenge.com", obtained by scraping; downloader repo carries no licence statement for the images; labels (aesthetic score distribution, semantic tags, photographic style) known from the paper — not verified from a primary source here. → **licence unknown; not redistributable**. | https://github.com/imfing/ava_downloader |

## 11. Open Images V7 (Google)

| Field | Value | Source |
|---|---|---|
| URL | https://storage.googleapis.com/openimages/web/factsfigures_v7.html ; download https://storage.googleapis.com/openimages/web/download_v7.html ; CVDF mirror https://github.com/cvdfoundation/open-images-dataset | all |
| Size | image-level labels over 20,638 classes (9,668 "trainable"); **validation 41,620 images / 618,184 human-verified image-level labels (390,797 positive)**; **test 125,436 images / 2,003,748 labels (1,319,751 positive)**. Class file `oidv7-class-descriptions.csv` = 20,931 rows (computed). | factsfigures ; computed |
| Licence | "The annotations are licensed by Google LLC under CC BY 4.0 license. The images are listed as having a CC BY 2.0 license." Per-image file `validation-images-with-rotation.csv` / `test-images-with-rotation.csv` columns: ImageID, Subset, OriginalURL, OriginalLandingURL, **License**, AuthorProfileURL, Author, Title, OriginalSize, OriginalMD5, Thumbnail300KURL, Rotation. **Computed: all 41,620 val and all 125,436 test rows have License = https://creativecommons.org/licenses/by/2.0/**. | factsfigures ; computed on the two CSVs |
| Redistribution / download | CC BY 2.0 → redistributable with attribution (Author + OriginalLandingURL available). Images are mirrored by CVDF: `aws s3 --no-sign-request sync s3://open-images-dataset/validation` (12 GB) / `…/test` (36 GB), or `tar/validation.tar.gz`; so Flickr take-downs do not affect availability of val/test. Label CSVs: `oidv7-val-annotations-human-imagelabels.csv`, `oidv7-test-annotations-human-imagelabels.csv` (Confidence 1/1.0 positive, 0.0 negative — both spellings occur). | download page ; CVDF repo |
| Relevant image-level classes present (MID → name; all exist) | Aurora, Milky way, Star, Galaxy, Constellation, Astronomical object, Moon, Full moon, Lunar eclipse, Night, Dawn, Dusk, Sunrise, Sunset, Evening, Morning, Backlighting, Silhouette, Landscape, Mountain, Coast, Beach, Forest, Grassland, Meadow, Prairie, Savanna, Steppe, Desert, Dune, Snow, Ice, Glacier, Iceberg, Lake, River, Waterfall, Pond, Stream, Wetland, Sky, Cloud, Bird, Flight, Flock, Herd, Mammal, Wildlife, Pet, Cattle, Sheep, Horse, Working animal, Reptile, Amphibian, Fish, Insect, Butterfly, Bee, Spider, Macro photography, Close-up, Flower, Plant, Leaf, Fungus, Mushroom, Portrait, Portrait photography, Selfie, Crowd, Person, Street, Concert, Festival, Wedding, Party, Ceremony, Parade, Sports, Team sport, Building, Architecture, Interior design, Room, Cityscape, Skyline, Downtown, Urban area, Monument, Sculpture, Statue, Memorial, Rural area, Village, Farm, Barn, Food, Drink, Vehicle, Car, Airplane, Boat, Still life, Still life photography, Art, Painting, Drawing, Illustration, Modern art, Graffiti, Screenshot, Document, Text, Pattern, Underwater, Coral reef, Aerial photography. **Absent**: Golden hour, Twilight, Blue hour, Long exposure, Astrophotography, Street/Landscape/Wildlife/Nature/Night/Food photography (only Portrait/Macro/Still-life photography exist), Countryside, Bokeh, Abstract art (exists but 1 positive). | `oidv7-class-descriptions.csv` grep |
| Per-label counts (computed: distinct images with human-verified positive label, val + test = 167,056 images) | see matrix; raw table in `oid_intersections.txt`. Examples: bird_flight = Bird∩Flight **204** (35 val + 169 test); herd_flock = (Bird∩Flock)∪(Mammal∩Herd) **243**; aurora **16**; astro **427**; moon **174**; city_lights = Night∩(Building\|Cityscape\|Skyline\|Street\|Skyscraper\|Downtown) **141**; Night **584**; Sunset\|Sunrise **210**; Dusk\|Dawn **203**; Underwater\|Coral reef\|Scuba **1,128**; Aerial photography **409**; Macro\|Close-up **11,797**; insect∩(Macro\|Close-up) **1,509**; fungi **394**; flower∩close-up **3,133**; portrait **1,105**; Crowd∩Person **315**; Street∩Person **268**; events **678**; Sports **9,818**; still_life **213**; art **7,598**; utility (Screenshot\|Document\|Whiteboard\|Diagram\|Map\|Receipt\|Menu\|Newspaper\|Handwriting) **1,782**; Pattern\|Abstract\|Modern art **2,426**. | `oid_intersections.txt` |
| Caveats | Labels are object/concept tags, not genre judgments: "Portrait" ≠ portrait photograph; "Sports" includes equipment; "Landscape" is a weak wide-shot proxy; negatives are human-verified too (usable as hard negatives). Image-level labels come from machine proposals verified by humans, so rare concepts (Aurora 8+8) are under-sampled. | factsfigures |

## 12. LAION-5B / Re-LAION-5B / COYO-700M — rejected

| Field | Value | Source |
|---|---|---|
| LAION | Metadata parquet under CC BY 4.0; "The images are under their copyright" — URLs + alt-text only, **no per-image licence field**. LAION-5B was withdrawn after the Stanford Internet Observatory report (2023-12-19); Re-LAION-5B released 2024-08-30 (research / research-safe, Apache 2.0 metadata), still URL-only, "released for research purposes". | https://laion.ai/blog/laion-5b/ ; https://laion.ai/blog/relaion-5b/ |
| COYO-700M | metadata CC BY 4.0; "The collected data (images and text) is subject to the license to which each content belongs"; no per-image licence attribute; Kakao Brain discourages commercial use without further processing. | https://github.com/kakaobrain/coyo-dataset |
| Why reject | no image licence, no image hosting, link rot; nothing to attribute; not usable as a redistributable ground-truth set. | — |

## 13. OATH — Oslo Aurora THEMIS training dataset

| Field | Value | Source |
|---|---|---|
| URL | http://tid.uio.no/plasma/oath/ (connection refused today; read via Wayback 2025-09-13); tarball http://tid.uio.no/plasma/oath/oath_v1.1_20181026.tgz (~500 MB) | http://web.archive.org/web/20250913045207/http://tid.uio.no/plasma/oath/ |
| Size / content | 5,824 PNGs `images/cropped_scaled/00001..05824.png` — **cropped, scaled THEMIS all-sky imager frames (scientific camera), not photographs**; `classification.csv` (2-class and 6-class labels + rotation), `files_origin.csv` (station, timestamp). | same |
| Labels | arc, diffuse, discrete, cloudy, moon, clear/no-aurora (6-class); aurora/no-aurora (2-class). Per-class counts (arc 774, discrete 1,102, diffuse 1,400, cloudy 817, moon 585, clear 1,082) come from the JGR paper as reported in search results — **unverified** against the paper text (Wiley returned 403). | https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2018JA025274 (not read) |
| Licence | "00_README … containing … lisense information" — inside the tarball only; **unverified**. Related Zenodo 11397580 (Johnson 2024) is CC BY 4.0 but contains only code (16.9 kB); Dryad sbcc2frft contains 23 GB of *classification labels* for ~700 M THEMIS frames, not the images. | Wayback page ; https://zenodo.org/records/11397580 ; https://datadryad.org/dataset/doi:10.5061/dryad.sbcc2frft |
| Coverage | aurora (and moon / clear-sky) only, in a non-photographic domain. | — |

## 14. SkyFinder

| Field | Value | Source |
|---|---|---|
| URL | https://zenodo.org/records/5884485 ; project https://mvrl.cse.wustl.edu/datasets/skyfinder/ | both |
| Size / content | 53 AMOS static webcams, 10.5 GB (53 zips + `skyfinder_masks.zip` 52.6 kB), one sky/ground binary mask per camera; ~90,000 images per the paper (search-result figure, unverified). Webcam frames across weather/illumination — not photographs; no genre labels. | Zenodo |
| Licence | **CC BY 4.0** (Zenodo record). | Zenodo |
| Coverage | none of our 40 labels directly; a possible source for light attribute (day/night) only if timestamps are used — unverified. | — |

## 15. BIOSCAN-5M

| Field | Value | Source |
|---|---|---|
| URL | https://huggingface.co/datasets/bioscan-ml/BIOSCAN-5M ; https://github.com/BIOSCAN-5M/BIOSCAN-5M (404 today) | HF |
| Size / content | >5 M "RGB JPEG image of an individual insect specimen" — **lab-photographed specimens on uniform background**, ~156 GB; cropped 256 px and original variants; HF `datasets`, Zenodo, Google Drive, Kaggle. | HF card |
| Licence | **CC BY 3.0** (HF card field). | HF card |
| Coverage | not field macro photos; unsuitable for insect_macro as a photo genre (fact: specimen imagery). | — |

## 16. Danish Fungi 2020 (DF20 / DF20-Mini)

| Field | Value | Source |
|---|---|---|
| URL | https://github.com/BohemianVRA/DanishFungiDataset ; https://sites.google.com/view/danish-fungi-dataset | both |
| Size / content | field photos from the Atlas of Danish Fungi (Svampeatlas); DF20 full-size ~110 GB, 300 px ~6.5 GB, DF20-Mini ~12.5 GB; metadata with Habitat, Substrate, Month (+ location/EXIF per paper abstract). Image counts (DF20 ≈ 296 K, Mini ≈ 33 K per paper) **unverified** here. | GitHub README ; https://arxiv.org/abs/2103.10107 |
| Licence (two sentences in the same README, quoted) | "The code and dataset is released under the BSD License. There is some limitations for commercial usage. In other words, the training data, metadata, and models are available only for non-commercial research purposes only." → treat as **research-only / NC**. | README §License |
| Download | HTTP tar.gz from ptak.felk.cvut.cz (no key). | README |
| Coverage | fungi only (all field photos; framing unlabeled). | — |

## 17. Oxford 102 Flowers

| Field | Value | Source |
|---|---|---|
| URL | https://www.robots.ox.ac.uk/~vgg/data/flowers/102/ | same |
| Size | 102 classes, 40–258 images/class, 8,189 images (HF mirror count); `102flowers.tgz` + segmentations + .mat splits. | VGG page ; https://huggingface.co/datasets/nelorth/oxford-flowers |
| Licence | **no licence statement on the VGG page**; HF mirror: "Unknown"; image origin not stated on the page → unknown. | both |
| Coverage | flower (close-up) only. | — |

## 18. Food-101

| Field | Value | Source |
|---|---|---|
| URL | https://data.vision.ee.ethz.ch/cvl/datasets_extra/food-101/ | same |
| Size | 101 classes × 1,000 = 101,000 images (750 train / 250 test per class), 5 GB tar.gz. | ETH page ; HF card |
| Licence | images from Foodspotting, "not owned by ETH Zurich"; "Any use beyond scientific fair use must be negociated with the respective picture owners according to the Foodspotting terms of use." → **research-only / unknown**. | https://huggingface.co/datasets/ethz/food101 (quoting the README) |
| Coverage | food_drink only. | — |

## 19. Stanford Cars / FGVC-Aircraft

| Field | Value | Source |
|---|---|---|
| Stanford Cars | original page https://ai.stanford.edu/~jkrause/cars/car_dataset.html → 404; Kaggle mirror → 401 without login; HF mirror `tanganke/stanford_cars` has no licence field; 16,185 images (widely cited; **unverified** here). Licence **unknown**. | fetch attempts |
| FGVC-Aircraft | https://www.robots.ox.ac.uk/~vgg/data/fgvc-aircraft/ — 10,200 images, 102 variants; "the images are made available exclusively for non-commercial research purposes. The original authors retain the copyright on the respective pictures" (airliners.net photographers). → **research-only**. | VGG page |
| Coverage | vehicle only. | — |

## 20. Photozilla (20 photography styles)

| Field | Value | Source |
|---|---|---|
| URL | https://trisha025.github.io/Photozilla/ (404 today; read via Wayback 2026-02-12); paper https://arxiv.org/abs/2106.11359 | Wayback ; arXiv |
| Size / labels | "over 990k images belonging to 10 different photographic styles" (~100 K/class): Aerial, Architecture, Event, Fashion, Food, Nature, Sports, Street, Wedding, Wildlife; plus 10 few-shot classes with 25 samples each: Abstract, Astrophotography, Automotive, Landscape, Lifestyle, Long Exposure, Panorama, Portrait, Travel, Underwater. | Wayback page |
| Licence / source | **No licence statement on the project page or in the arXiv abstract**; image source sites not stated there; the "Link: Photozilla Dataset" download target was not fetched. A search snippet claims "Creative Commons license and copyright-free images" — **unverified**. | Wayback page ; arXiv abstract |
| Coverage (if licence were confirmed) | aerial (framing), street, event, sport, food_drink, building/architecture, wildlife (unsplit), astro (25), portrait (25), underwater (25), abstract (25), landscape (25, unsplit). | — |

---

## Coverage matrix

Cell = images obtainable under **CC0 / CC BY** (Commons cells add the BY-SA share; Places/SUN/iNat-2021/DF20/Food-101/Aircraft are research-only and marked as such). Counts are *computed* unless marked. "—" = no matching label. OID = Open Images V7 val+test human-verified positives (all CC BY 2.0). Commons = root-category direct file count (recursive counts in bold where computed); licence mix ≈ 40–60 % CC0/PD/CC BY, rest CC BY-SA (sampled). iNat = research-grade observations with ≥1 CC0/CC BY photo (taxon only, framing/pose unknown). Places = val images at 100/class (research-only). SUN = ≥100/class (research-only). Unsplash Lite / Flickr / Pexels / Pixabay = platform licence, counts unverified (marked "key/TSV needed").

| Label | OID V7 (CC BY 2.0) | Wikimedia Commons (CC0/BY/BY-SA, per-file) | iNat open data (CC0/CC BY) | Places365 val (research-only) | SUN397 (research-only) | Others |
|---|---|---|---|---|---|---|
| **wildlife** | | | | | | |
| bird_portrait | Bird 3,337 (portrait vs habitat not separable) | Portraits of birds: no category found; "Birds" tree huge, unsplit | Aves 4,565,703 (unsplit) | — | — | Photozilla "Wildlife" ~100 K, licence unverified |
| bird_flight | Bird∩Flight **204** | Birds in flight 471 direct / **5,717** recursive(2) ; Bird flight 44 | none (no annotation) | — | — | — |
| bird_habitat | (see bird_portrait) | unsplit | unsplit | — | — | — |
| mammal_portrait | Mammal∩Wildlife 1,072; Mammal any 24,793 (includes humans/pets) | Wildlife photography 451 / **562** rec. (sample 48/50 BY-SA); Mammals 38 direct (taxonomic tree unsplit) | Mammalia 549,441 (unsplit) | — | — | — |
| mammal_habitat | (see above) | unsplit | unsplit | — | — | — |
| other_animal | Reptile\|Amphibian\|Fish 2,152 | Fish 132; Reptiles/Amphibians 0 direct (subcats only) | Reptilia 457,225 · Amphibia 270,721 · Actinopterygii 306,329 · Arachnida 457,102 | — | — | — |
| herd_flock | (Bird∩Flock)∪(Mammal∩Herd) **243** | Flocks of birds in flight 374 / **2,517** rec.; Herds 147 / **1,454** rec.; Flocks of sheep 249 | none | — | — | — |
| domestic | Pet\|Cattle\|Sheep\|Horse\|Working animal 9,209 | Pets 336; Dogs 500; Cats 138; Livestock 156 | captive Aves+Mammalia 45,995; captive all 665,851 (all quality grades) | — (kennel/outdoor, stable, corral = venues) | — | — |
| **landscape** | | | | | | |
| mountain | 1,769 | Mountains 515 (+55 subcats) | — | ~9 cats ≈ 900 | ~11 cats ≥1,100 | — |
| coast | Coast\|Beach 955 | Coasts 482; Beaches 2,403 | — | ~9 cats ≈ 900 | ~10 cats ≥1,000 | — |
| freshwater | 1,935 | Lakes 994; Rivers 1,277; Waterfalls 1,228; Ponds 1,503; Wetlands 720 | — | ~13 cats ≈ 1,300 | ~15 cats ≥1,500 | — |
| forest | 719 | Forests 5,630 | — | 6 cats = 600 | 8 cats ≥800 | — |
| grassland | 3,288 | Grasslands 464; Meadows 659 | — | ~7 cats ≈ 700 | ~7 cats ≥700 | — |
| desert | 241 | Deserts 918 / **1,778** rec.; Dunes 873 | — | 4 cats = 400 | 3 cats ≥300 | — |
| snow_ice | 2,256 | Ice 1,074; Glaciers 187 / **2,879** rec.; Snow 0 direct/86 subcats | — | ~10 cats ≈ 1,000 | ~10 cats ≥1,000 | — |
| sky | Sky−Building−Person 11,431 | Sky 2,593; Clouds 0 direct/51 subcats | — | sky = 100 | sky ≥100 | SkyFinder: masks only (CC BY 4.0) |
| **night** | | | | | | |
| astro | Star\|Milky way\|Galaxy\|Constellation\|Astro. object 427 | Milky Way Galaxy 1,112 / **2,315** rec.; Night sky 886 / **1,607** rec.; Star trails 197 / **628** rec. | — | — | — | Photozilla Astrophotography 25 (unverified) |
| aurora | **16** | Aurora borealis 95 direct / **1,628** rec.; Aurora australis 63 / **303** rec. | — | — | — | OATH 5,824 all-sky frames (non-photo; licence unverified) |
| moon | Moon\|Full moon\|Lunar eclipse 174 | Full moon 1,357 / **2,621** rec.; Moon 811 | — | — | — | — |
| city_lights | Night∩(Building\|Cityscape\|Skyline\|Street\|Skyscraper\|Downtown) 141 | Cities at night 35 / **392** rec.; Light pollution 213 / **2,081** rec.; Night photography 1,446 / **2,320** rec. | — | — | — | — |
| **people** | | | | | | |
| portrait | Portrait\|Portrait photography\|Selfie 1,105 | Portrait photographs 4,409; Portraits 14,047 (incl. paintings); Selfies 1,261 / **3,705** rec. | — | — | — | Photozilla Portrait 25 (unverified) |
| group | Crowd∩Person 315 | Group portraits 1,442 / **2,679** rec. (sample: 27/50 PD, i.e. many historical scans); Group photographs 2,884 | — | — | — | — |
| street | Street∩Person 268 | Street photography 1,090 / **1,556** rec. (sample 49/50 CC0/PD/CC BY) | — | street/alley/crosswalk = scenes, people not guaranteed | same | Photozilla Street ~100 K (unverified) |
| event | Concert\|Festival\|Wedding\|Party\|Ceremony\|Parade 678 | Concerts 3,548; Festivals 3,017; Weddings 693 | — | — | — | Photozilla Event/Wedding ~100 K each (unverified) |
| sport | Sports 9,818 (Team sport\|Ball game\|Running\|Swimming\|Cycling\|Skiing 3,519) | Sports 2,513 (+80 subcats); Sports photography 10 | — | venues only (stadium/*, football_field, …) | venues only | Photozilla Sports ~100 K (unverified) |
| **macro** | | | | | | |
| flower | Flower∩(Macro\|Close-up) 3,133; Flower any 10,070 | Flowers 6 direct / 75 subcats (taxonomic) | Plantae + Flowers annotation 1,114,619 | — | — | Oxford 102: 8,189 (licence unknown) |
| plant | (Plant\|Leaf\|Tree)∩Close-up −Flower 1,818 | taxonomic tree, unsplit | Plantae 9,858,812 | — (botanical_garden etc. are scenes) | — | — |
| fungi | Fungus\|Mushroom 394 | Fungi 1,091 (+67 subcats) | Fungi 725,145 | — | — | DF20 ~296 K (research-only) |
| insect_macro | (Insect\|Butterfly\|Bee\|Spider\|Dragonfly\|Beetle\|Moth)∩(Macro\|Close-up) 1,509; any 1,957 | Insects 444; Butterflies 84; Macro photographs 1,587 / **2,386** rec. (mixed subjects; sample 41/50 BY-SA) | Insecta 5,913,759 (framing unknown) | — | — | BIOSCAN-5M: specimens, not field macro (CC BY 3.0) |
| **architecture** | | | | | | |
| building | 14,004 | Buildings 2,434; Architecture 1,816 | — | ~40 cats ≈ 4,000 | similar | Photozilla Architecture ~100 K (unverified) |
| interior | Room\|Interior design\|Living room\|Kitchen\|Bedroom 3,857 | Interiors 1,264 / **3,311** rec. | — | 161 indoor classes = 16,100 | indoor classes (count unverified) | — |
| cityscape | Cityscape\|Skyline\|Downtown\|Urban area 475 | Cityscapes 416 / **1,913** rec. | — | ~15 cats ≈ 1,500 | similar | — |
| monument | Monument\|Sculpture\|Statue\|Memorial 1,853 | Monuments 117 / **153** rec. (depth 1; deeper national trees not counted) | — | ~10 cats ≈ 1,000 | similar | — |
| rural | Rural area\|Village\|Farm\|Barn 1,355 | Villages 259 / **732** rec.; Rural areas 1 | — | ~14 cats ≈ 1,400 | similar | — |
| **food** | | | | | | |
| food_drink | Food\|Drink 15,603 | Food photography 776 / **1,039** rec. (sample 50/50 BY-SA); Food 101 direct/91 subcats | — | — (food venues only) | — | Food-101 101,000 (research-only) ; Photozilla Food ~100 K (unverified) |
| vehicle | 30,011 | Vehicles 7 direct / 145 subcats | — | — (car_interior, cockpit… = interiors) | — | FGVC-Aircraft 10,200 (research-only); Stanford Cars 16,185 (licence unknown) |
| still_life | Still life\|Still life photography 213 | Still life **1,628** rec. (mostly paintings; sample 37/50 BY-SA) | — | — | — | — |
| art | 7,598 (incl. Sculpture) | Paintings 5,255; Abstract art 1,600 | — | — (art_gallery = room) | — | — |
| **other** | | | | | | |
| utility | Screenshot\|Document\|Whiteboard\|Diagram\|Map\|Receipt\|Menu\|Newspaper\|Handwriting 1,782 | Screenshots 12,062 / **50,087** rec.; Screenshots of software 577; Documents 1,485 | — | — | — | — |
| abstract | Pattern\|Abstract art\|Modern art 2,426 | Abstract art 1,600 / **1,847** rec.; Textures 913 | — | — | — | Photozilla Abstract 25 (unverified) |
| **light** | | | | | | |
| day | (complement of the below; not labelled) | not labelled | not labelled | not labelled | not labelled | — |
| golden_hour | no class; proxy Sunset\|Sunrise 210 (wider proxy incl. Dusk\|Dawn\|Evening\|Morning\|Backlighting 598) | Golden hour 0; Sunsets 999 / **7,562** rec.; Sunrises 807 | — | — | — | — |
| blue_hour | no class; proxy Dusk\|Dawn 203 | Blue hour 87 / **262** rec. | — | — | — | — |
| night | Night 584 | Night 1,640; Night photography 1,446 | — | none | none | — |
| **setting** | | | | | | |
| outdoor | derivable from landscape classes only | not labelled | (all field photos, effectively outdoor) | 204 outdoor classes = 20,400 | via hierarchy (unverified) | — |
| indoor | proxy Room\|Interior design\|… 3,857 | Interiors 1,264 | — | 161 indoor classes = 16,100 | via hierarchy (unverified) | — |
| underwater | Underwater\|Coral reef\|Scuba diving 1,128 | Underwater photographs 285 / **4,957** rec. | Actinopterygii 306,329 (not all underwater) | underwater/ocean_deep = 100 | underwater/coral_reef ≥100 | Photozilla Underwater 25 (unverified) |
| **framing** | | | | | | |
| close_up | Macro photography\|Close-up 11,797 | Macro photographs 1,587; Close-up photographs 547 | — | — | — | — |
| medium | not labelled | not labelled | — | — | — | — |
| wide | proxy Landscape 958 | Landscape photography 918 / **4,770** rec.; Landscapes 693 | — | scene datasets are mostly wide by construction (not labelled) | same | — |
| aerial | Aerial photography 409 | Aerial photography 108; Aerial photographs 55 / **928** rec. | — | none | none | Photozilla Aerial ~100 K (unverified) |

Platform APIs (Unsplash / Pexels / Pixabay / Flickr) are omitted from the columns because no counts were taken: they cover every label by keyword search, but Unsplash ToS §8 bars ML use of site images (Lite dataset excepted, internal-only, no redistribution); Pexels/Pixabay ToS §8 bar scraping/bulk copying "including … for machine learning purposes"; Flickr ToS bars caching beyond "reasonable periods" although the photo-level CC licence itself permits reuse — counts need an API key.

## Addendum — Commons recursive counts (computed 2026-09-25)

Distinct files in the category plus its subcategories to the stated depth (`list=categorymembers`, cmtype=file|subcat), and the `LicenseShortName` distribution of the first 50 direct files of the root category. Source: `commons_recursive_part1.txt`, `commons_recursive_part2.txt`.

| Category | files | depth | licence sample (50 root files) |
|---|---|---|---|
| Aurora borealis | 1,628 | 2 | CC0 3 · PD 10 · CC BY 20 · BY-SA 17 |
| Aurora australis | 303 | 2 | PD 35 · CC0 2 · CC BY 6 · BY-SA 6 · other 1 |
| Birds in flight | 5,717 | 2 | CC0 2 · PD 5 · "No restrictions" 4 · CC BY 10 · BY-SA 29 |
| Flocks of birds in flight | 2,517 | 2 | CC0 5 · PD 10 · CC BY 8 · BY-SA 26 · other 1 |
| Milky Way Galaxy | 2,315 | 1 | not sampled (API shape error) |
| Night sky | 1,607 | 1 | CC0 4 · PD 3 · CC BY 31 · BY-SA 12 |
| Star trails | 628 | 1 | CC0 3 · PD 4 · CC BY 14 · BY-SA 28 · OGL 1 |
| Light pollution | 2,081 | 1 | PD 7 · CC BY 11 · BY-SA 32 |
| Blue hour | 262 | 1 | CC0 4 · PD 3 · CC BY 19 · BY-SA 24 |
| Cities at night | 392 | 1 | CC BY 11 · BY-SA 24 (sample returned 35) |
| Night photography | 2,320 | 1 | CC0 1 · PD 2 · "No restrictions" 1 · CC BY 28 · BY-SA 18 |
| Full moon | 2,621 | 1 | CC0 3 · PD 2 · "No restrictions" 1 · CC BY 23 · BY-SA 21 |
| Underwater photographs | 4,957 | 1 | CC0 2 · PD 5 · CC BY 7 · BY-SA 36 |
| Aerial photographs | 928 | 1 | CC0 4 · PD 5 · CC BY 10 · BY-SA 31 |
| Street photography | 1,556 | 1 | CC0 1 · PD 13 · CC BY 35 · BY-SA 1 |
| Selfies | 3,705 | 1 | CC0 4 · PD 3 · CC BY 18 · BY-SA 25 |
| Group portraits | 2,679 | 1 | CC0 5 · PD 27 · "No restrictions" 1 · CC BY 3 · BY-SA 13 · FAL 1 |
| Herds | 1,454 | 1 | CC0 1 · PD 5 · CC BY 9 · BY-SA 35 |
| Cityscapes | 1,913 | 1 | CC0 4 · PD 2 · CC BY 7 · BY-SA 37 |
| Glaciers | 2,879 | 1 | CC0 3 · PD 15 · "No restrictions" 1 · CC BY 6 · BY-SA 25 |
| Deserts | 1,778 | 1 | CC0 1 · PD 4 · CC BY 7 · BY-SA 38 |
| Macro photographs | 2,386 | 1 | CC BY 9 · BY-SA 41 |
| Wildlife photography | 562 | 1 | PD 1 · CC BY 1 · BY-SA 48 |
| Food photography | 1,039 | 1 | BY-SA 50 |
| Still life | 1,628 | 1 | CC0 2 · PD 5 · "No restrictions" 2 · CC BY 4 · BY-SA 37 |
| Interiors | 3,311 | 1 | CC0 4 · PD 1 · CC BY 10 · BY-SA 35 |
| Monuments | 153 | 1 | CC0 5 · PD 2 · CC BY 3 · "Attribution" 1 · BY-SA 39 |
| Villages | 732 | 1 | CC0 4 · PD 2 · CC BY 9 · BY-SA 35 |
| Abstract art | 1,847 | 1 | CC0 13 · PD 2 · CC BY 9 · BY-SA 26 |
| Screenshots | 50,087 | 1 | CC0 6 · PD 2 · CC BY 3 · GPL 4 · "Attribution" 1 · BY-SA 34 |
| Sunsets | 7,562 | 1 | CC0 6 · CC BY 15 · BY-SA 29 |
| Landscape photography | 4,770 | 1 | CC0 6 · PD 1 · CC BY 14 · BY-SA 29 |

Reading the samples: the CC0/PD/CC BY share ranges from 0/50 (Food photography) to 49/50 (Street photography); across the 32 sampled categories it is roughly 35–45 % on average, with CC BY-SA the single largest bucket. Applying that to a category count gives the order of magnitude of CC0/CC BY files; an exact figure needs a per-file `extmetadata` pass over the whole category (≈ 1 API call per 50 files).

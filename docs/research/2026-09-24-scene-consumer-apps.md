# Consumer photo apps: auto-organisation categories (primary sources)

Researched 2026-09-24. Facts only; no proposal. Everything not backed by a fetched primary page is marked **unverified**.

Legend for the "kind" column used below:
- **content** = what is in the picture (scene/object/event), produced by a classifier
- **attribute** = capture/media/library metadata (format, camera mode, user action), not a classifier output
- **mixed** = a collection that blends the two

---

## 1. Google Photos

### 1a. Library API `ContentCategory` enum (developer-facing taxonomy)

Source: https://developers.google.com/photos/library/reference/rest/v1/mediaItems/search (section `ContentCategory`)
Second source (same list, with usage constraints): https://developers.google.com/photos/library/guides/apply-filters

26 real values plus `NONE`. Quoted descriptions from the API reference:

| Value | Description (verbatim) | kind |
|---|---|---|
| `NONE` | Default content category. This category is ignored when any other category is used in the filter. | — |
| `LANDSCAPES` | Media items containing landscapes. | content |
| `RECEIPTS` | Media items containing receipts. | content |
| `CITYSCAPES` | Media items containing cityscapes. | content |
| `LANDMARKS` | Media items containing landmarks. | content |
| `SELFIES` | Media items that are selfies. | content (straddles attribute) |
| `PEOPLE` | Media items containing people. | content |
| `PETS` | Media items containing pets. | content |
| `WEDDINGS` | Media items from weddings. | content (event) |
| `BIRTHDAYS` | Media items from birthdays. | content (event) |
| `DOCUMENTS` | Media items containing documents. | content |
| `TRAVEL` | Media items taken during travel. | content (event/context) |
| `ANIMALS` | Media items containing animals. | content |
| `FOOD` | Media items containing food. | content |
| `SPORT` | Media items from sporting events. | content (event) |
| `NIGHT` | Media items taken at night. | content (scene condition) |
| `PERFORMANCES` | Media items from performances. | content (event) |
| `WHITEBOARDS` | Media items containing whiteboards. | content |
| `SCREENSHOTS` | Media items that are screenshots. | content (straddles attribute) |
| `UTILITY` | Media items considered utility, including documents, screenshots, whiteboards etc. | content, umbrella |
| `ARTS` | Media items containing art. | content |
| `CRAFTS` | Media items containing crafts. | content |
| `FASHION` | Media items related to fashion. | content |
| `HOUSES` | Media items containing houses. | content |
| `GARDENS` | Media items containing gardens. | content |
| `FLOWERS` | Media items containing flowers. | content |
| `HOLIDAYS` | Media items taken of holidays. | content (event) |

Note: the task brief's example list omitted `SPORT`; it is in the enum. The filters guide's prose count ("30") does not match the enumerated list; the API reference enumerates 26 + `NONE`.

Hierarchy: flat enum, with one documented umbrella: `UTILITY` "including documents, screenshots, whiteboards etc." The filters guide adds: "Utility photos cover a broad range of media. This category generally includes items the user has captured to perform some task and is unlikely to want after that task is completed."

Single vs multi-label: multi-label. A media item can be in several categories; the filter docs say included/excluded category sets "are ORed", up to 10 included and 10 excluded per request, and a category cannot be in both lists. `UTILITY` overlapping `DOCUMENTS`/`SCREENSHOTS`/`WHITEBOARDS` is itself proof that items carry more than one category.

Attributes in the same API (not content):

| Filter | Values (verbatim) | kind |
|---|---|---|
| `MediaType` | `ALL_MEDIA` "Treated as if no filters are applied. All media types are included." / `VIDEO` "All media items considered videos, including movies created using Google Photos app." / `PHOTO` "Media considered photos, including .bmp, .gif, .ico, .jpg, .tiff, .webp and special types." | attribute; "Only one media type is supported." |
| `Feature` | `NONE` / `FAVORITES` "Media items marked as favorites in the Google Photos app." | attribute (user action) |
| `DateFilter` | up to 5 dates and 5 date ranges per request | attribute |

Filters guide detail on formats: PHOTO "can be any of several image formats: BMP, JPG, GIF, PNG, HEIC, TIFF, ICO, WEBP" plus "iOS live photos and panoramas"; VIDEO lists 3GP, MMV, 3G2, MOD, ASF, MOV, AVI, MP4, DIVX, MPG, M2T, MTS, M2TS, TOD, M4V, WMV, MKV plus VR videos and animations.

Status caveat: https://developers.google.com/photos/support/updates states that as of 31 March 2025 "You can now only list, search, and retrieve albums and media items that were created by your app." So `mediaItems.search` with these filters no longer queries a user's whole library; the enum still documents Google's taxonomy.

### 1b. Google Photos app (user-facing)

Source: https://support.google.com/photos/answer/15235862?hl=en&co=GENIE.Platform%3DDesktop ("Search by people, things & places in your photos")

- Collections reachable from the side panel named on the page: "People & pets", "Documents", "Places", "Videos", "Albums".
- Search examples on the page: "Alice and me laughing", "colorful sunsets in Mexico", "Emma at the playground", "drinking tea by the fireplace", "me", "a pet's vet invoice by the vet clinic's name". Quotation marks force exact-text matching "in filenames, camera models, captions, or text within photos".
- The page does not enumerate the "Things" grid shown in the app's Search tab. **unverified**: no Google help page found that lists those chips; the API `ContentCategory` enum above is the closest published enumeration.

Source: https://support.google.com/photos/answer/14187361?hl=en&co=GENIE.Platform%3DAndroid ("Sort your documents into useful albums")

- Documents collection auto-sorts "into useful albums like ID, receipts, and event information" (only those three are named; "like" implies the list is not exhaustive). Auto-archive hides document photos "older than 30 days" from the main grid. Documents albums are "only available for backed up photos".

Hierarchy in the app: top level = People & pets / Places / Documents / Videos (+ Albums, Memories); Documents has sub-albums (ID, receipts, event information, ...). Whether "Things" has documented sub-levels: **unverified**.

Attribute vs category in the app: Videos = attribute; Documents/People & pets/Places = content; "Screenshots" appears as a Collection in the app UI but is not named on the fetched help pages (**unverified**).

Source checked but not useful for categories: https://support.google.com/photos/answer/6128838 (face groups only).

---

## 2. Apple Photos (iOS / macOS) and the Vision framework

### 2a. Search categories (user-facing, iOS 27 guide)

Source: https://support.apple.com/en-sg/guide/iphone/iph392d77d5f/ios ("Search for photos and videos on iPhone"; the article body was extracted from the served HTML)

Verbatim list of what the search field accepts:
- Date (month or year)
- Place (city or state)
- Business names (museums, for example)
- Category (beach or sunset, for example)
- Events (sports games or concerts, for example)
- People or pets you have named in your photo library
- Text (an email address or phone number, for example)
- Caption
- "Tip: You can narrow your search by including a media type in the search field, such as Video or Portrait."
- With Apple Intelligence: natural-language descriptions ("Maya skateboarding in a tie-dye shirt").
- Filters on results: "Favorites or Edited"; sort by Added / Captured.

Apple does not publish the list of searchable "Category" words; it names beach and sunset as examples. The underlying vocabulary is the Vision taxonomy (2c) plus synonyms (2d).

### 2b. Collections: Media Types and Utilities (user-facing)

Sources:
- https://support.apple.com/en-in/guide/iphone/iph8530ff6a2/ios ("Locate photos and videos by media type on iPhone") - names only "Portrait mode photos or time-lapse videos" as examples; no full list.
- https://support.apple.com/guide/photos/pht76fbca67a/mac ("Find screenshots, Live Photos, and more by media type on Mac") - collections appear only "if you have that media type in your photo library", examples "Bursts, Panoramas, and RAW", Portrait, Screenshots.
- https://support.apple.com/guide/iphone/find-receipts-qr-codes-edited-photos-iph995007f21/ios (iOS 27 text): "you can revisit photos that you captured, or recently edited, saved, or shared. You can also find photos based on their content-like identity documents, receipts, handwriting, illustrations, and QR codes." Also: "Some Utilities collections, like Hidden and Recently Deleted, are locked by default", and a "Recovered" collection.
- https://support.apple.com/en-nz/guide/iphone/iph995007f21/18.0/ios/18.0 (iOS 18 text): "recently edited, saved, viewed, or shared ... documents, receipts, handwriting, illustrations, and QR codes." (iOS 18 says "documents"; iOS 27 says "identity documents".)
- https://support.apple.com/guide/iphone/merge-duplicate-photos-and-videos-iph1978d9c23/ios: "Tap Utilities, then tap Duplicates." "If you don't have any duplicate photos or videos in your library, the Duplicates collection doesn't appear."

Utilities collections confirmed from the guide pages (kind in brackets):
- Content-based: Identity documents / Documents, Receipts, Handwriting, Illustrations, QR Codes  [content]
- Library-state: Recently Edited, Recently Saved, Recently Viewed (iOS 18 wording), Recently Shared, Hidden, Recently Deleted, Duplicates, Recovered  [attribute]. The iOS 27 sentence also says "photos that you captured"; whether that is a collection named Captured is **unverified**.
- Other Utilities collections commonly seen in the app (Imports, Maps, Unable to Upload): **unverified** from the guide; `smartAlbumUnableToUpload` exists in PhotoKit (below).

Media Types collections: Apple's guides give only examples. The primary enumeration is PhotoKit's smart-album subtypes, which is what the Photos app surfaces:

Source: https://developer.apple.com/documentation/photos/phassetcollectionsubtype (fetched via the doc JSON endpoint). "Smart Album Types", verbatim descriptions:

| `PHAssetCollectionSubtype` | Description | UI name (inferred, **unverified**) |
|---|---|---|
| `smartAlbumVideos` | groups all video assets | Videos |
| `smartAlbumSelfPortraits` | photos and videos captured using the device's front-facing camera | Selfies |
| `smartAlbumLivePhotos` | all Live Photos assets | Live Photos |
| `smartAlbumDepthEffect` | images captured using the Depth Effect camera mode | Portrait |
| `smartAlbumPanoramas` | all panorama photos | Panoramas |
| `smartAlbumTimelapses` | all time-lapse videos | Time-lapse |
| `smartAlbumSlomoVideos` | all Slow-Mo videos | Slo-mo |
| `smartAlbumCinematic` | all cinematic photo assets | Cinematic |
| `smartAlbumSpatial` | (no description published) | Spatial |
| `smartAlbumBursts` | all burst photo sequences | Bursts |
| `smartAlbumScreenshots` | images captured using the device's screenshot function | Screenshots |
| `smartAlbumScreenRecordings` | videos captured using the device's screenrecordings function | Screen Recordings |
| `smartAlbumAnimated` | all image animation assets | Animated |
| `smartAlbumRAW` | all RAW assets | RAW |
| `smartAlbumLongExposures` | Live Photos where the Long Exposure variation is enabled | Long Exposure |
| `smartAlbumFavorites` | assets the user marks as favorites | Favorites |
| `smartAlbumAllHidden` | assets hidden from the Moments view | Hidden (Utilities) |
| `smartAlbumRecentlyAdded` | recently added assets | Recently Saved (Utilities) |
| `smartAlbumUnableToUpload` | assets that the system can't upload to iCloud | Unable to Upload (Utilities) |
| `smartAlbumUserLibrary` | assets that originate in the user's own library | (not a UI collection) |
| `smartAlbumGeneric` | without a more-specific subtype | — |

All Media Types are **attributes** (capture mode / format / camera), not classifier outputs. Companion per-asset flags: https://developer.apple.com/documentation/photos/phassetmediasubtype - `photoPanorama`, `photoHDR`, `photoScreenshot`, `photoLive`, `photoDepthEffect`, `photoAnimation`, `videoCinematic`, `videoStreamed`, `videoHighFrameRate`, `videoTimelapse`, `videoScreenRecording`, `spatialMedia` (OptionSet, so several can apply to one asset).

Other top-level collections named in Apple's iOS 18 press release (https://www.apple.com/newsroom/2024/06/ios-18-makes-iphone-more-personal-capable-and-intelligent-than-ever/): "browse by themes, like recent days or trips"; carousel features "favorite people, pets, places". People & Pets, Recent Days, Trips as exact collection names: **unverified** from that page (the guide TOC links "Find your travel photos and videos" and "Find and name people and pets", consistent with those names).

Visual Look Up (per-photo lookup, not library organisation) names its domains: "architectural landmarks, popular statues, famous art, plants, pets, books, and more ... food". Source: https://support.apple.com/guide/iphone/identify-objects-in-your-photos-and-videos-iph21c29a1cf/ios

### 2c. Vision `VNClassifyImageRequest` (the on-device scene/object taxonomy)

Sources:
- https://developer.apple.com/documentation/vision/vnclassifyimagerequest - "A request to classify an image." "This type of request produces a collection of VNClassificationObservation objects that describe an image." Topics list `supportedIdentifiers()`, `results`, `knownClassifications(forRevision:)`, and one revision: `VNClassifyImageRequestRevision1`.
- https://developer.apple.com/documentation/vision/vnclassifyimagerequest/supportedidentifiers() - "Returns the classification identifiers that the request supports in its current configuration." `func supportedIdentifiers() throws -> [String]`
- https://developer.apple.com/documentation/vision/vnclassificationobservation - `identifier`, `hasPrecisionRecallCurve`, `hasMinimumPrecision(_:forRecall:)`, `hasMinimumRecall(_:forPrecision:)`.
- WWDC 2019 session 222 "Understanding Images in Vision Framework", https://developer.apple.com/videos/play/wwdc2019/222/ (as extracted from the transcript by the fetch tool, not checked against the raw transcript):
  - "the network we're talking about exposing here is in fact the same network we ourselves use to power the photo search experience."
  - "We've also developed it to identify over a thousand different categories of objects."
  - "The taxonomy has a hierarchical structure with directional relationships between classes ... a class like dog might have children like Beagle, Poodle, Husky, and other sub-breeds of dogs."
  - "this is a multi-label network capable of identifying multiple objects in a single image ... you actually get an array of observations, one for every class in the taxonomy and its associated confidence ... they won't sum to 1".
  - Taxonomy rules: "classes must be visually identifiable. That is, we avoid more abstract concepts like holiday or festival. We also avoid any classes that might be considered controversial or offensive as well as those to do with proper names, ... adjectives, or basic shapes. Finally, we omit occupations".
  - Filtering by per-class operating point: `hasMinimumPrecision(0.5, forRecall: 0.9)` (high recall) / `hasMinimumRecall(0.8, forPrecision: 0.95)` (high precision).

Identifier list: Apple does not publish it as a page; it is obtained by calling the API. Third-party dump of `knownClassifications(forRevision: VNClassifyImageRequestRevision1)`: https://gist.github.com/ktustanowski/56c0d7541813868fed4aceb60ab5d149 - 1303 identifiers (a second independent dump, https://gist.github.com/alexdong/5da51b09d4fc07139f6ce98ceb8705ab, has 1302 lines, consistent). The full list is in Appendix A. These are **third-party**, Revision1; whether later OS releases changed the set is **unverified**.

Structure of the identifiers: flat snake_case strings; the hierarchy Apple describes is not exposed as parent pointers in the API. Name-level hints only: `animal`, `mammal`, `bird`, `insect`, `fish`, `reptile`, `cat`, `adult_cat`, `dog`, `beagle`, `poodle`, `husky`, `plant`, `flower`, `food`; scenes such as `beach`, `mountain`, `cityscape`, `outdoor`, `sky`, `snow`, `water`; utility-like classes `document`, `receipt`, `screenshot`, `whiteboard`; people classes `people`, `adult`, `child`, `baby`. 193 of the 1303 identifiers contain an underscore (compound names such as `alligator_crocodile`, `prairie_dog`, `puffer_fish`).

Kind: all **content**. Multi-label with independent per-class confidences.

### 2d. Apple ML research: how the taxonomy reaches Photos search

Source: https://machinelearning.apple.com/research/on-device-scene-analysis ("A Multi-Task Neural Architecture for On-Device Scene Analysis", 7 June 2022). Verbatim:
- "Visual content search is an important use case that is serviced through a fixed taxonomy image tagger."
- "Detecting food, mountains, beaches, birthdays, pets, or hikes helps build a personalized understanding of the user and their interests."
- "Synonyms of tags in the taxonomy are searchable, while a word-embedding model is used to mitigate null queries suggesting the closest searchable concept."
- "Prior to iOS and macOS Ventura, ANSA was designed around a frozen backbone trained with a multilabel classification objective."
- "The scene tags from ANSA are ingested in the Photos knowledge graph".
- Image-language embeddings later enabled queries like "people seated around a table" that a fixed taxonomy cannot express.
- Other example categories on the page: "beaches, mountains, and sunsets", "receipts, documents, and repair references".
- The post gives no taxonomy size.

Source: https://machinelearning.apple.com/research/recognizing-people-photos ("Recognizing People in Photos Through Private On-Device Machine Learning", 28 July 2021): People album; on-device knowledge graph of "important groups of people, frequent places, past trips, events". No mention of pets on that page.

Apple summary of hierarchy: top level in the app = Collections (Recent Days, People & Pets, Memories, Trips, Albums, Media Types, Utilities, Pinned ...); Media Types and Utilities are lists of flat collections; scene search is a flat multi-label vocabulary with an internal (unexposed) parent/child taxonomy.

---

## 3. Others (brief)

### 3a. Samsung Gallery

Sources: https://www.samsung.com/ae/support/mobile-devices/samsung-gallery-using-the-search-function/ and https://www.samsung.com/ph/support/mobile-devices/how-to-use-the-search-function-in-gallery-app/ and https://www.samsung.com/us/support/answer/ANS10002535/

- "organizes your photos and videos into categories such as people, locations, and objects" (content); "automatically assign tags to many of your photos by analysing what is in the image".
- Media-type filters named: Photos, Videos, Screenshots, GIFs, Documents (attribute-ish; "Documents" here is a media/content bucket).
- Search by tag, album name, location, date.
- Named category sections seen in the app (e.g. Scenes, Expressions, Suggested tags) and any tag vocabulary: **unverified**; not on Samsung's help pages.
- Multi-label: tags are plural per photo; no explicit statement.

### 3b. Microsoft OneDrive

Sources: https://support.microsoft.com/en-us/office/find-your-photos-quickly-with-tags-in-onedrive-00230883-d8b1-4efb-9df6-91903c32156e and https://support.microsoft.com/en-us/office/find-your-photos-quickly-with-intelligent-searches-in-onedrive-4b5e9300-38ea-4afa-8b42-a56e8b3c72d7

- "OneDrive automatically creates tags for things it recognizes. Sometimes it makes mistakes, but you can remove or edit the tags for a photo." Tags are editable, plural per photo (multi-label).
- Location tags are a separate attribute; "Location tags can reveal personal information so be careful when using them!"
- Intelligent search examples: "mountain camping", "London conference booth", "running in Sedona", "outdoor wedding green dress"; search also covers text in photos.
- No published tag vocabulary. **unverified** beyond that.

### 3c. Amazon Photos

Source: https://www.amazon.co.uk/gp/help/customer/display.html?nodeId=202094300 ("Learn More about Image Tagging"; the US page returned 503 during research)

- "Tag Photos allows Amazon Photos to tag your photos and videos with keywords describing objects, actions, scenes, and other things in your photos. For example, you can search for "happy," "car," "beaches," "smiles," and more."
- "People grouping ... automatically group similar faces"; naming enables per-person search; also uses "approximate age and gender".
- Tags are plural per photo (multi-label); no published vocabulary.
- "People, Places, Things" as the three search facets and the Illinois default-off note came from a search snippet of a page that could not be fetched: **unverified**.

---

## 4. Cross-product comparison (as found)

| Product | Content taxonomy | Size | Multi-label | Hierarchy exposed | Attributes kept separate |
|---|---|---|---|---|---|
| Google Photos API | `ContentCategory` | 26 (+NONE) | yes (ORed sets, `UTILITY` umbrella) | flat, one umbrella | `MediaType` (PHOTO/VIDEO), `Feature` (FAVORITES), dates |
| Google Photos app | People & pets, Places, Documents (ID / receipts / event info ...), Things (unpublished) | n/a | yes | 2 levels (Documents has sub-albums) | Videos, Albums |
| Apple Photos app | search "Category" words (beach, sunset ...), Utilities content buckets (identity documents, receipts, handwriting, illustrations, QR codes) | unpublished | yes | Collections > Media Types / Utilities (2 levels) | Media Types = capture attributes; Utilities also holds library-state buckets |
| Apple Vision | `VNClassifyImageRequest` identifiers | 1303 (Rev1, third-party dump); Apple: "over a thousand" | yes, independent confidences | internal parent/child (dog > beagle), not exposed | n/a |
| Samsung Gallery | people / locations / objects + tags | unpublished | yes | unpublished | Photos, Videos, Screenshots, GIFs, Documents |
| OneDrive | auto tags "for things it recognizes" | unpublished | yes (editable list) | none documented | location tags |
| Amazon Photos | keywords for "objects, actions, scenes" | unpublished | yes | none documented | people grouping |


## Appendix A: VNClassifyImageRequest identifiers (Revision1, 1303, third-party dump)

Source: https://gist.github.com/ktustanowski/56c0d7541813868fed4aceb60ab5d149

abacus, accordion, acorn, acrobat, adult, adult_cat, agriculture, aircraft, airplane, airport, airshow, alley, alligator_crocodile, almond, ambulance, amusement_park, anchovy, angelfish, animal, ant, antipasti, anvil, apartment, apple, appliance, apricot, apron, aquarium, arachnid, arch, archery, arena, armchair, art, arthropods, artichoke, arugula, asparagus, athletics, atm, atv, auditorium, aurora, australian_shepherd, automobile, avocado, axe, baby, backgammon, backhoe, backpack, bacon, badminton, bag, bagel, baked_goods, baklava, balcony, ball, ballet, ballet_dancer, ballgames, balloon, balloon_hotair, banana, banner, bar, barbell, barge, barn, barnacle, barracuda, barrel, baseball, baseball_bat, baseball_hat, basenji, basket_container, basketball, basset, bath, bathrobe, bathroom, bathroom_faucet, bathroom_room, beach, beagle, bean, beanie, bear, bed, bedding, bedroom, bee, beef, beehive, beekeeping, beer, beet, begonia, bell, bell_pepper, belltower, bellydance, bench, bernese_mountain, berry, bib, bichon, bicycle, billboards, billiards, binoculars, bird, birdhouse, birthday_cake, biryani, biscotti, biscuit, bison, blackberry, bleachers, blender, blizzard, blocks, blossom, blue_sky, blueberry, boar, board_game, boat, boathouse, bobcat, bodyboard, bongo_drum, bonsai, book, bookshelf, boot, bottle, bouquet, bowl, bowling, bowtie, boxing, branch, brass_music, bread, breakdancing, brick, brick_oven, bride, bridesmaid, bridge, briefcase, broccoli, broom, brownie, bruschetta, bubble_tea, bucket, building, bulldog, bulldozer, bullfighting, bungee, burrito, bus, butter, butterfly, cabinet, cableway, cactus, cage, cake, cake_regular, cakestand, calculator, calendar, caliper, camel, camera, camping, candle, candlestick, candy, candy_cane, candy_other, canine, canoe, cantaloupe, canyon, caprese, car, car_seat, caramel, cardboard_box, carnation, carnival, carousel, carrot, cart, carton, cashew, casino, casserole, cassette, castle, cat, caterpillar, cauliflower, cave, cd, celebration, celery, celestial_body, celestial_body_other, cellar, cello, centipede, cephalopod, cereal, ceremony, cetacean, chainsaw, chair, chair_other, chairlift, chaise, chalkboard, chameleon, chandelier, chart, checkbook, cheerleading, cheese, cheesecake, cheetah, cherry, chess, chestnut, chewing_gum, chihuahua, child, chimney, chinchilla, chives, chocolate, chocolate_chip, chopsticks, christmas_decoration, christmas_tree, chrysanthemum, cigar, cigarette, cilantro, circuit_board, circus, citrus_fruit, cityscape, clam, clarinet, classroom, cliff, cloak, clock, clock_tower, closet, clothesline, clothespin, clothing, cloudy, clover, clown, clownfish, cockatoo, cocktail, coconut, coffee, coffee_bean, coin, coleslaw, collie, compass, computer, computer_keyboard, computer_monitor, computer_mouse, computer_tower, concert, conch, condiment, conference, consumer_electronics, container, convertible, conveyance, cookie, cookware, coral_reef, cord, corgi, corkscrew, corn, cornflower, cosmetic_tool, costume, cougar, coupon, cow, cowboy_hat, coyote_wolf, crab, cranberry, crane_construction, crate, credit_card, creek, crepe, crib, cricket_sport, croissant, crosswalk, crowd, cruise_ship, crutch, cubicle, cucumber, cup, cupcake, currency, curry, curtain, cutting_board, cycling, dachshund, daffodil, dahlia, daikon, daisy, dalmatian, dam, dancing, dandelion, dartboard, dashboard, daytime, decanter, deck, decoration, decorative_plant, deejay, deer, desert, desk, dessert, diagram, dial, diaper, dice, dill, dining_room, dinosaur, diorama, dirt_road, disco_ball, dishwasher, diskette, diving, doberman, dock, document, dog, doll, dolphin, dome, domicile, domino, donkey, donut, door, dove, dragon_parade, dragonfly, dressage, drink, drinking_glass, driveway, drone_machine, drum, dumbbell, dumpling, durian, eagle, earmuffs, easel, easter_egg, edamame, egg, eggplant, electric_fan, elephant, elevator, elk, embers, engine_vehicle, entertainer, envelope, equestrian, escalator, eucalyptus_tree, evergreen, extinguisher, eyeglasses, fairground, falafel, farm, fedora, feline, fence, fencing_sport, ferns, ferret, ferris_wheel, fig, figurine, fire, firecracker, fireplace, firetruck, fireworks, fish, fishbowl, fishing, fishtank, flag, flagpole, flame, flamingo, flan, flashlight, flipchart, flipper, flower, flower_arrangement, flute, folding_chair, foliage, fondue, food, foosball, football, footwear, forest, fork, forklift, formula_one_car, fountain, fox, frame, fried_chicken, fried_egg, fries, frisbee, frog, frozen, frozen_dessert, fruit, fruitcake, furniture, gamepad, games, garage, garden, gargoyle, garlic, gas_mask, gastropod, gazebo, gears, gecko, gerbil, german_shepherd, geyser, gift, gift_card, gingerbread, giraffe, glacier, glove, glove_other, go_kart, goat, goggles, goldfish, golf, golf_ball, golf_club, golf_course, gown, graduation, graffiti, grain, grand_prix, grape, grapefruit, grass, grater, grave, green_beans, greenhouse, greyhound, grill, grilled_chicken, groom, guacamole, guava, guitar, gull, guppy, gymnastics, gyoza, habanero, ham, hamburger, hammer, hammock, hamster, handwriting, hangar, hangglider, harbour, hardhat, harp, hat, haze, headgear, headphones, health_club, hedgehog, helicopter, helmet, henna, herb, heron, high_chair, high_heel, hiking, hill, hippopotamus, hockey, holly, honey, honeydew, hoodie, hookah, horse, horseshoe, hospital, hotdog, hound, hourglass, house_single, houseboat, housewares, hula, hummingbird, hummus, hunting, hurdle, husky, hydrant, hyena, ice, ice_cream, ice_skates, ice_skating, iceberg, igloo, iguana, illustrations, insect, interior_room, interior_shop, irish_wolfhound, iron_clothing, island, ivy, jack_o_lantern, jack_russell_terrier, jacket, jacuzzi, jalapeno, jar, jeans, jeep, jello, jelly, jellyfish, jetski, jewelry, jigsaw, jockey_horse, joystick, jug, juggling, juice, juicer, jungle, kangaroo, karaoke, kayak, kebab, keg, kettle, keypad, kickboxing, kilt, kimono, kitchen, kitchen_countertop, kitchen_faucet, kitchen_oven, kitchen_room, kitchen_sink, kite, kiteboarding, kitten, kiwi, knife, koala, kohlrabi, koi, lab_coat, ladle, ladybug, lake, lamp, lamppost, land, lantern, laptop, laundry_machine, lava, leash, leek, lemon, lemongrass, lemur, leopard, leotard, lettuce, library, license_plate, lifejacket, lifesaver, light, light_bulb, lighter, lighthouse, lightning, lily, lime, limousine, lion, lionfish, liquid, liquor, living_room, lizard, llama, loafer, lobster, lollipop, luggage, lychee, lynx, macadamia, machine, mackerel, magazine, mailbox, malamute, malinois, mallet, mammal, mandarine, mango, mangosteen, mangrove, manhole, map, maple_tree, margarita, marigold, marshmallow, marsupial, martial_arts, martini, mask, mast, mastiff, matches, material, matzo, measuring_tape, meat, meatball, medal, media, medicine, megalith, megaphone, melon, microphone, microscope, microwave, military_uniform, milkshake, millipede, mistletoe, mitten, moccasin, mojito, mollusk, money, monitor_lizard, monorail, monument, moon, moose, mop, moss, moth, motocross, motorcycle, motorhome, motorsport, mountain, mousetrap, mower, muffin, mug, museum, mushroom, music, musical_instrument, mussel, mustard, naan, nachos, nascar, necktie, nectarine, nest, newfoundland, newspaper, night_sky, nightclub, nut, oak_tree, oar, oatmeal, obelisk, ocean, office_supplies, omelet, onion, optical_equipment, oranges, orchard, orchestra, orchid, organ_instrument, origami, ostrich, otter, outdoor, oven, owl, oyster, pacifier, paella, paintball, paintbrush, painting, palm_tree, pan, pancake, panda, papaya, paper_bag, parachute, parade, parakeet, parasailing, park, parking_lot, parrot, passionfruit, passport, pasta, pastry, path, patio, payphone, pea, peach, peacock, peanut, pear, pecan, pelican, pen, penguin, people, pepper_veggie, pepperoni, peregrine, performance, pergola, persimmon, petunia, phone, piano, pickle, pie, pier, pierogi, pig, pigeon, piggybank, pillow, pineapple, ping_pong, pipe, pistachio, pita, pitbull, pizza, plant, plate, play_card, playground, pliers, plum, podium, poinsettia, poker, pole, police_car, polka_dots, polo, pomegranate, pomeranian, poncho, poodle, pool, popcorn, popsicle, porch, porcupine, portal, porthole, pot_cooking, potato, poultry, power_saw, prairie_dog, pretzel, printed_page, printer, propeller, puck, pudding, puffer_fish, puffin, pug, pulley, pumpkin, puppet, purse, putt, puzzles, pylon, pyramid, pyrotechnics, python, quesadilla, quinoa, rabbit, raccoon, racquet, radish, rafting, railroad, rainbow, rake, rambutan, ramen, rangoli, raptor, raspberry, rat, ratchet, rattlesnake, raven, raw_glass, receipt, record, recreation, red_envelope, red_wine, refrigerator, reptile, restaurant, retriever, rhinoceros, rhubarb, rice, rice_field, rickshaw, ridgeback, rim, rink, risotto, river, road, road_other, road_safety_equipment, rock_climbing, rocket, rocks, rodent, rodeo, roe, rollercoaster, rollerskates, rollerskating, rolling_pin, roof, rope, rose, rosemary, rotisserie, rottweiler, roulette, rowboat, rugby, ruins, sack, saddle, safety_vest, sailboat, saint_bernard, salad, salami, salmon, samba, samosa, sand, sand_dune, sandal, sandcastle, sandpiper, sandwich, sangria, santa_claus, sardine, sari, satay, sauerkraut, sausage, saxophone, scallop, scarab, scarecrow, scarf, schnauzer, scissors, scone, scooter, scoreboard, scorpion, scrambled_eggs, screenshot, screwdriver, scuba, seabass, seafood, seahorse, seal, sealion, seashell, seasonings, seat, seaweed, seed, seesaw, semi_truck, sequoia, sesame, setter, sewing, shark, shawarma, shed, sheep, sheepdog, shellfish, shellfish_prepared, shipyard, shoes, shopping_cart, shore, shower, shrub, sidewalk, sign, silo, singer, skateboard, skateboarding, skatepark, skating, skeleton, ski_boot, ski_equipment, skiing, skull, skunk, sky, skydiving, skyscraper, sled, sledding, slide_toy, smokestack, smoking_item, smoothie, snail, snake, snake_other, snapdragon, snapper, sneaker, snorkeling, snow, snowball, snowboard, snowboarding, snowman, snowmobile, snowshoe, soccer, sock, soda, sofa, softball, solar_panel, sombrero, souffle, soup, souvlaki, spaghetti, spaniel, spareribs, sparkler, sparkling_wine, sparrow, spatula, speakers_music, speedboat, spice, spider, spiderweb, spinach, spoon, sport, sports_equipment, sportscar, spotlight, springroll, sprinkler, squash_sport, squirrel, stadium, stained_glass, stairs, starfish, starfruit, statue, steak, steamer_cookware, stereo, stethoscope, sticky_note, stingray, stir_fry, stool, stopwatch, storefront, stork, storm, stove, straw_drinking, straw_hay, strawberry, street, street_sign, streetcar, stretcher, string_instrument, stroller, structure, strudel, stuffed_animals, submarine_water, sugar_cube, suit, suitcase, sumo, sun, sunbathing, sundial, sunfish, sunflower, sunflower_seeds, sunglasses, sunhat, sunset_sunrise, surfboard, surfing, sushi, suv, swan, swimming, swimsuit, swing_playground, swivel_chair, sword, swordfish, syringe, tabbouleh, table, tableware, tachometer, taco, taffy, tambourine, tapas, tapioca_pearls, taro, tattoo, tea_drink, teapot, teen, telescope, television, tempura, tennis, tent, tequila, teriyaki, terrarium, terrier, textile, theater, thermometer, thermos, thermostat, thunderstorm, tiara, ticket, tiger, timepiece, tiramisu, tire, toad, toaster, toaster_oven, toilet_seat, tomato, tool, toolbox, tornado, tortilla, tortoise, toucan, tower, toy, track_rail, tractor, traffic_light, trail, train, train_real, train_station, train_toy, trampoline, tramway, trash_can, treadmill, tree, tricycle, tripod, trombone, trophy, trout, truck, trumpet, tuba, tulip, tuna, tunnel, turmeric, turntable, turtle, tuxedo, typewriter, ukulele, umbrella, underwater, ungulates, urchin, utensil, vacuum, van, vase, vegetable, vegetation, vehicle, vehicle_toy, videogame, vineyard, violin, vizsla, volcano, volleyball, vulture, waffle, wagon, wakeboarding, wallet, walrus, warship, wasabi, washbasin, watch, water, water_body, watercraft, waterfall, watering_can, watermelon, watermill, waterpolo, watersport, waterways, wedding, wedding_cake, wedding_dress, weight_scale, weimaraner, wetland, wetsuit, whale, wheat, wheel, wheelbarrow, wheelchair, whisk, white_bread, white_wine, whiteboard, willow, winch, wind_turbine, windmill, window, windsurfing, wine, wine_bottle, winter_sport, wonton, wood_natural, wood_processed, woodpecker, woodwind, workout, worm, wreath, wrench, wrestling, xylophone, yacht, yarn, yoga, yogurt, yolk, zebra, zoo, zucchini


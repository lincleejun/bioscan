# 照片分类提案：scene 阶段的细分类目

状态：**提案，未实现，准确率未验证**（CLAUDE.md：没有真实照片 eval 的准确率说法一律 "unverified"）。
调研原始记录（每条结论附来源 URL，未能一手核实的标 unverified）：同目录的
`2026-09-24-scene-consumer-apps.md`、`2026-09-24-scene-photographer-tools.md`、`2026-09-24-scene-academic.md`；
本文只保留结论和关键来源。记录里提到的 "local xxx.txt" 是调研时抓下来的原文副本，没有进仓库。
抽查：Google `ContentCategory` 枚举已由我本人重新抓取核对（26 值 + NONE）。

## 0. 现状

`scene` 阶段（`bioscan/plugins/scene/`）对整帧 SigLIP2 向量做零样本分类：8 个平级标签
landscape, people, wildlife, macro, architecture, food, night, other。每个标签是几条英文提示词的平均文本向量；
`wildlife` 不用提示词，直接取 gate 的 bird + mammal 概率，其余标签在剩下的概率里做 softmax。
`select` 归约器按顶层标签分桶、每桶取前 `per_category` 张；`night` 桶免除 `underexposed`；`landscape` 额外测地平线。
标签是选项（`[profile.album.options.scene.labels]`），album 可以换自己的。

## 1. 现有实现对任何新分类法的约束

| 约束 | 出处 | 对提案的影响 |
|---|---|---|
| 所有文本标签在**一个** softmax 里竞争 | `stage.py: scores()` | 40 个平级标签会把概率摊薄，且 `per_category=10` 会变成最多 400 张 pick。必须分两级：粗组分桶、细标签描述 |
| 只允许**一个**无提示词标签（gate 标签） | `__init__.py: check()` | bird / mammal 不能各占一个空标签；野生动物的细分要靠 identify 已经算出的 `boxes`（kind、面积、数量）加组内提示词 |
| 地平线只在标签**字面等于** `landscape` 时触发 | `stage.py: LANDSCAPE` | landscape 拆细后要改成按粗组判断，否则地平线静默失效 |
| `night` 今天是类别，`select.waive` 默认 `{"night": ["underexposed"]}` | `plugins/select/__init__.py` | 夜景其实是**光线属性**：夜间风景在平级 softmax 里两头摇摆。属性化后 waive 要能按属性键 |
| 标签名 `[a-z0-9_]+`，至少 2 个标签 | `check()` | 下面所有名字都遵守 |
| 输出 `label` + `scores`，`select` 只读 `products.scene.label` | `cull.py:168` | 新增字段（`group`、`attributes`）不破坏现有读法 |
| 评测只有 `scene_acc`（顶层标签 == 真值） | `cull.py: row_scene` | 需要细→粗映射，让现有 8 标签真值继续可测 |

## 2. 业界与学术怎么分（调研结论）

### 2.1 消费级相册
- **Google Photos**（Library API `ContentCategory`，26 值 + NONE；[reference](https://developers.google.com/photos/library/reference/rest/v1/mediaItems/search)）：
  LANDSCAPES, CITYSCAPES, LANDMARKS, HOUSES, GARDENS, FLOWERS, ANIMALS, PETS, PEOPLE, SELFIES, FOOD, NIGHT, TRAVEL,
  WEDDINGS, BIRTHDAYS, HOLIDAYS, SPORT, PERFORMANCES, ARTS, CRAFTS, FASHION, DOCUMENTS, RECEIPTS, WHITEBOARDS, SCREENSHOTS, UTILITY（伞形）。
  **多标签**（过滤条件 OR），平级；媒体类型（PHOTO/VIDEO）、收藏、日期是**独立属性**，不进内容分类。App 侧顶层只有 People & pets / Places / Documents（子相册 ID、receipts、event info）/ Videos；"Things" 网格无公开列表。
- **Apple Photos**：Vision `VNClassifyImageRequest` ~1300 类（第三方 dump 1303，Apple 说 "over a thousand"），**多标签、置信度独立**，内部有父子层级（dog > beagle）但 API 不暴露；同一网络驱动相册搜索（[WWDC19 s222](https://developer.apple.com/videos/play/wwdc2019/222/)、[Apple ML 2022](https://machinelearning.apple.com/research/on-device-scene-analysis)）。分类原则："visually identifiable"，回避节日等抽象概念、专名、形容词、职业。UI 把 **Media Types**（Portrait, Panorama, Burst, RAW, Screenshot…，即 PhotoKit smart album 子类型）和 **Utilities**（receipts, handwriting, illustrations, QR codes, duplicates…）单独成层，与内容搜索分开。
- Samsung / OneDrive / Amazon：只说 "people, locations, objects" 级别的自动标签，无公开词表。

### 2.2 摄影师工具与开源相册
- **PhotoPrism**（`internal/ai/classify/rules.yml`，2025-10）：1218 条 ImageNet 类 → 252 个策展标签 → 58 个类目（animal 505 条、vehicle、water、people、food、architecture、reptile、wildlife、bird、insect、outdoor、landscape、nature…），类目是标签到标签的 join，深度 1–3 的 DAG，多标签带置信度。
- **Peakto**（[features](https://cyme.io/en/peakto/features/photo-search/)）：14 个题材（Architecture, Astro-photography, Wildlife, Automotive, Events & Wedding, Fashion, Food & Drinks, Nature, People, Portrait, Screenshots, Sport, Street, Water & Underwater）+ **正交维度**：风格（Abstract, Aerial, Close Up, Night）、人数（Alone, InDuo, Small/Large Group）、光线（Bright, Dark, High/Low Contrast…）、色彩。这是最接近摄影师视角的公开方案。
- **500px**（API 文档，2019）：30 个平级题材，单标签：Abstract, Aerial, Animals, Black and White, City and Architecture, Concert, Family, Fashion, Fine Art, Food, Journalism, Landscapes, Macro, Nature, Night, People, Performing Arts, Sport, Still Life, Street, Transportation, Travel, Underwater, Urban Exploration, Wedding…
- **Immich**：无自动分类（CLIP 语义搜索 + OCR + 人脸）；ImageNet 标签 2023-12 删掉了。**digiKam**：直接挂 1000 类 / COCO 80 类原始标签，无策展。
- **Aftershoot / Narrative / FilterPixel**：只有选片桶（Selected / Blur / Closed Eyes / Duplicates），没有题材分类。Lightroom Sensei 标签无公开词表（unverified）。

### 2.3 学术场景数据集
- **Places365**（[categories_places365.txt](https://raw.githubusercontent.com/CSAILVision/places365/master/categories_places365.txt)、
  README 链接的层级表）：365 个叶类，161 indoor / 204 outdoor；三层层级：L1 = indoor / outdoor natural / outdoor man-made，
  L2 = 16 组（indoor：shopping and dining 44、workplace 35、home or hotel 31、transportation 16、sports and leisure 17、cultural 30；
  outdoor natural：water ice snow 33、mountains hills desert sky 14、forest field jungle 31、man-made elements 32；
  outdoor man-made：transportation 25、cultural or historical 31、sports fields parks leisure 36、industrial 11、houses cabins gardens farms 37、
  commercial buildings shops markets cities 37）。**多隶属**：33 类跨两个 L1，63 类跨多个 L2。
- **SUN397**：同一套 16 个 L2 组名（IJCV 图 23），L1 三分 177 / 89 / 171（多隶属）。
- **AVA**：14 个摄影风格（Murray 2012），与题材正交。
- 这些层级是**场景**（在哪儿）的分法；摄影题材（拍什么、怎么拍）与之交叉：Places 的 "forest, field, jungle" 对应本提案 landscape 组的 forest/grassland，
  "water, ice, snow" 对应 coast/freshwater/snow_ice，"mountains, hills, desert, sky" 对应 mountain/desert/sky。

### 2.4 共同点
1. **内容分类与拍摄属性分开**：Google 的 MediaType/Feature、Apple 的 Media Types/Utilities、Peakto 的风格/光线维度都不混进题材里。
2. **两级**：Apple 内部层级、PhotoPrism 标签→类目、Google Documents 子相册、Places365/SUN 的 macro → leaf。
3. **多标签**是消费级默认；单标签只见于 500px 这种题材投稿。bioscan 的 `select` 要分桶，需要一个主标签，但可以同时输出全部得分（今天已经这样）。
4. 面向摄影师的集合（Peakto、500px）比消费级少很多"生活杂项"（收据、白板、截图），多"题材"（astro、macro、street、underwater、aerial）。

## 3. 提案

面向本仓库的对象：一位野生动物摄影师的 RAW 档案。所以**野生动物最细、风景其次、人物/建筑/生活够用即可**；消费级的收据/截图/白板归到一个 `utility` 标签就够。

### 3.1 结构：8 个粗组 → 40 个细标签 + 3 个正交属性

- **粗组（group）** 是 `select` 分桶、`waive`、地平线、现有真值映射用的单位。8 个，和今天的 8 个一一对应，所以现有 `scene_acc` 真值不用重标。
- **细标签（label）** 是 `products.scene.label` 报出来的主标签，一张照片一个；全部得分照常在 `scores` 里。
- **概率怎么分**（只此一处定义）：
  1. `wildlife` 组的份额 = gate 的 `wildlife_gate` 概率之和，和今天一样，不用提示词。
  2. 其余 7 组的 32 个细标签在**一个** softmax 里分剩下的 `1 − 份额`；粗组得分 = 组内细标签得分之和（按组求和不摊薄，因为组内标签的概率质量仍归本组）。
  3. `wildlife` 组内的 8 个细标签**另起**一个组内 softmax（只在这 8 个标签的提示词之间），再叠加 §3.3 的 box 规则；它们不参与第 2 步的竞争。
  所以 wildlife 的细分**必须改代码**，光改 TOML 做不到（今天的代码把唯一的空标签当 gate 标签，且所有带提示词的标签都在同一个 softmax 里）。
- **属性（attributes）** 各自独立 softmax，与题材正交：`light`（day / golden_hour / blue_hour / night）、`setting`（outdoor / indoor / underwater）、`framing`（close_up / medium / wide / aerial）。

| 粗组 | 细标签 | 判定方式 |
|---|---|---|
| `wildlife`（= 今天 wildlife） | `bird_portrait` `bird_flight` `bird_habitat` `mammal_portrait` `mammal_habitat` `other_animal` `herd_flock` `domestic` | gate 的 bird+mammal（+other_animal）份额，组内按 identify 的 `boxes` 规则 + 提示词细分（§3.3） |
| `landscape` | `mountain` `coast` `freshwater` `forest` `grassland` `desert` `snow_ice` `sky` | 提示词 |
| `night` | `astro` `aurora` `moon` `city_lights` | 提示词（组保留是因为 `waive` 键在它上；光线本身另有 `light` 属性） |
| `people` | `portrait` `group` `street` `event` `sport` | 提示词 |
| `macro`（改叫 macro_flora 也可） | `flower` `plant` `fungi` `insect_macro` | 提示词；gate 判 other_animal 的昆虫走 wildlife.other_animal，gate 没抓到的走这里 |
| `architecture` | `building` `interior` `cityscape` `monument` `rural` | 提示词 |
| `food` | `food_drink` | 提示词 |
| `other`（杂项） | `utility` `abstract` `vehicle` `still_life` `art` | 提示词；softmax 总有最大值，今天也没有"都不像"的兜底，这里也不加。vehicle / still_life / art 放这里而不是 food：今天的阶段把它们判成 other，scene tier 的第一次 baseline（2026-09-25）证实了这一点，放 food 组会让 group_acc 把真值错误算成阶段错误 |

细→粗映射就是表的第一列；旧 8 标签真值按粗组算 `group_acc`，新真值按细标签算 `scene_acc`。

### 3.2 完整 TOML（可直接放进 `bioscan.toml` 试跑提示词部分）

`groups`、`attributes`、`wildlife_rules` 三个键是**新选项**（§3.4）；`labels` 与今天格式相同。

**今天就能试的子集**（不改代码）：把下面 `labels` 里 wildlife 组的 8 个标签删掉，改成一个空标签 `wildlife = []`，再删掉 `groups` /
`attributes` / `wildlife_rules` 三段和 `select.by`。这样是 1 个 gate 标签 + 32 个平级提示词标签，能看非野生动物提示词的质量；
但 `select` 会按 33 个标签分桶（`per_category=10` 最多 330 张 pick），只适合看 CSV / HTML 里的标签，不适合拿来选片。

```toml
[profile.album.options.scene]
wildlife_gate = ["bird", "mammal", "other_animal"]

[profile.album.options.scene.groups]
wildlife     = ["bird_portrait", "bird_flight", "bird_habitat", "mammal_portrait", "mammal_habitat", "other_animal", "herd_flock", "domestic"]
landscape    = ["mountain", "coast", "freshwater", "forest", "grassland", "desert", "snow_ice", "sky"]
night        = ["astro", "aurora", "moon", "city_lights"]
people       = ["portrait", "group", "street", "event", "sport"]
macro        = ["flower", "plant", "fungi", "insect_macro"]
architecture = ["building", "interior", "cityscape", "monument", "rural"]
food         = ["food_drink"]
other        = ["utility", "abstract", "vehicle", "still_life", "art"]

[profile.album.options.scene.labels]
# wildlife: 组份额来自 gate；组内先按 wildlife_rules，规则不命中的用这些提示词
bird_portrait   = ["a close-up photo of a bird perched", "a frame-filling portrait of a bird", "a bird on a branch, sharp eye"]
bird_flight     = ["a bird flying with wings spread", "a bird in flight against the sky", "a raptor soaring"]
bird_habitat    = ["a small bird in a wide natural scene", "a bird far away in its habitat", "a wetland with a distant bird"]
mammal_portrait = ["a close-up photo of a wild mammal", "a deer or a fox looking at the camera", "a frame-filling wild animal"]
mammal_habitat  = ["a wild mammal small in a wide landscape", "an animal far away on a hillside", "an elk in a meadow at distance"]
other_animal    = ["a photo of a reptile or an amphibian", "a lizard, a snake, a frog or a turtle", "a fish or a marine animal"]
herd_flock      = ["a large flock of birds", "a herd of animals", "many animals together in one frame"]
domestic        = ["a pet dog or a cat", "farm animals, cows, sheep or horses", "a domestic animal"]
# landscape
mountain    = ["a photo of mountains and peaks", "an alpine valley", "a mountain ridge with clouds"]
coast       = ["a photo of the sea and a beach", "ocean waves on rocks", "a coastline with cliffs"]
freshwater  = ["a lake with reflections", "a river or a stream", "a waterfall"]
forest      = ["a photo of a forest", "trees in a woodland", "a path through the woods"]
grassland   = ["a wide meadow or prairie", "rolling green fields", "a savanna or grassland"]
desert      = ["a desert with sand dunes", "a canyon of red rock", "a dry arid landscape"]
snow_ice    = ["a snow-covered landscape", "a glacier or an iceberg", "a frozen winter scene"]
sky         = ["a colourful sunset or sunrise sky", "dramatic storm clouds", "a rainbow over a landscape"]
# night
astro       = ["the milky way over a landscape", "a night sky full of stars", "astrophotography, star trails"]
aurora      = ["the northern lights, aurora borealis", "green aurora curtains in the night sky"]
moon        = ["a photo of the moon", "a full moon over a landscape", "a lunar eclipse"]
city_lights = ["city lights at night", "a night street with neon signs", "a skyline after dark, long exposure"]
# people
portrait    = ["a portrait of one person", "a headshot with a blurred background", "a person looking at the camera"]
group       = ["a group of people posing", "a family photo", "friends together at a table"]
street      = ["a candid street photo with people", "people walking in a city street", "a busy market with people"]
event       = ["a wedding ceremony", "a party or a celebration", "a concert or a stage performance"]
sport       = ["an athlete in action", "a sports game in a stadium", "people running, cycling or climbing"]
# macro
flower       = ["a close-up photo of a flower", "a blossom with petals filling the frame", "wildflowers, macro"]
plant        = ["a close-up of leaves or a plant", "moss, ferns or bark detail", "a tree in blossom"]
fungi        = ["a mushroom on the forest floor", "fungi growing on a log", "toadstools, macro"]
insect_macro = ["a macro photo of an insect", "a butterfly or a bee on a flower", "a dragonfly, close-up"]
# architecture
building  = ["a photo of a building exterior", "modern architecture, glass and steel", "a house or a church facade"]
interior  = ["an indoor photo of a room", "the interior of a building", "a hall with columns and arches"]
cityscape = ["a city skyline", "an urban scene with many buildings", "a view over rooftops"]
monument  = ["a historic monument or a landmark", "ancient ruins", "a castle or a temple"]
rural     = ["a farm with a barn", "a village in the countryside", "a rural road with fences"]
# food
food_drink = ["a photo of food", "a photo of a meal, a snack or a dessert", "a photo of a drink"]   # #45: short prompts, was "food on a plate / a meal at a restaurant / coffee or wine"
vehicle    = ["a photo of a car", "a train, a boat or an aircraft", "a motorbike or a bicycle"]
still_life = ["a still life photo of objects", "a product photo of an object", "an arrangement of ornaments"]   # #45: no "table" / "items", they drew plated food
art        = ["a painting or a mural", "a sculpture or a statue", "graffiti on a wall"]
# other
utility  = ["a screenshot or a document", "a receipt, a whiteboard or a sign with text", "a scan of a page"]
abstract = ["an abstract photo of textures and patterns", "blurred colours and shapes", "a minimalist abstract composition"]

[profile.album.options.scene.wildlife_rules]
portrait_area = 0.08     # 最佳 box 面积 ≥ 该比例 → *_portrait，否则 *_habitat
flock_boxes   = 4        # box 数 ≥ 该值 → herd_flock

[profile.album.options.scene.attributes.light]
day         = ["a photo taken in daylight", "bright midday light"]
golden_hour = ["warm low sunlight at golden hour", "long shadows at sunset light"]
blue_hour   = ["blue hour twilight", "dusk, just after sunset"]
night       = ["a photo taken at night", "a dark scene lit by artificial light"]

[profile.album.options.scene.attributes.setting]
outdoor    = ["an outdoor photo", "a scene in open air"]
indoor     = ["an indoor photo", "inside a building"]
underwater = ["an underwater photo", "a diver's view under the sea"]

[profile.album.options.scene.attributes.framing]
close_up = ["a close-up, the subject fills the frame", "a macro or a tight portrait"]
medium   = ["a medium shot with some surroundings", "the subject and its environment"]
wide     = ["a wide shot of a whole scene", "a wide-angle landscape"]
aerial   = ["an aerial photo from a drone", "a top-down view from above"]

[profile.album.options.select]
per_category = 10
by = "group"                              # 新选项：按粗组分桶（默认）或 "label"
waive = { night = ["underexposed"], "light=night" = ["underexposed"] }   # 键可以是组名或 属性=值
```

### 3.3 野生动物细分：规则优先，提示词补位

组份额仍是 gate 的 `wildlife_gate` 概率之和（不变）。组内 8 个细标签的得分 = 份额 × 组内 softmax（只在这 8 个标签的提示词之间），主标签按下面的顺序定：
1. `boxes` 数 ≥ `flock_boxes` → `herd_flock`。
2. 组内 softmax 里 `bird_flight` 或 `domestic` 最高且高于 0.5 → 取它（这两个不能从 box 推出）。
3. 否则按最佳 box 的 `kind` × 面积：bird/mammal × (`area ≥ portrait_area` ? portrait : habitat)；other_animal → `other_animal`。
4. 没有 box（gate 救援也没找到）→ 组内 softmax 的最高项。

不确定项（unverified）：`portrait_area = 0.08` 是猜的；`quality` 阶段用 0.005 判 `subject_too_small`，两者要在真实相册上一起看。

### 3.4 需要的代码改动（小，向后兼容）

1. `scene` 选项加 `groups`（缺省 = 每个标签自成一组，即今天的行为）、`attributes`（缺省空）、`wildlife_rules`；manifest `reads` 加 `boxes`。
2. `scores()` 改成 §3.1 的三步：gate 份额给含 gate 标签的组，其余标签一个 softmax 按组求和，gate 组内另起 softmax；输出加 `group`、`group_scores`、`attributes: {name: {label, scores}}`。
3. 地平线判断改为 `group == "landscape"`。
4. `check()`：gate 标签从"唯一的空标签"改为 `groups` 里含 `wildlife_gate` 份额的那一组（选项 `gate_group`，默认 `wildlife`）；没有 `groups` 时保持今天的语义（唯一的空标签），完全向后兼容。
5. `select` 加 `by`（group | label），`waive` 键支持 `属性=值`。
6. `cull.row_scene` 加 `group_acc`；`scene` 真值列允许写细标签或粗组名。
文本编码次数：40 + 11 条标签各 2–4 条提示词，每个标签集只编码一次并缓存（现有机制），每 chunk 多两三次小矩阵乘，速度影响可忽略（unverified，CI 上量）。

## 4. 评测计划（做完才能说准确率）

1. **先跑现有真值不回退**：album 合成集全是 wildlife，`group_acc` 应保持 ≥ 0.80 的 standard（`culling.album.all.scene_acc` 改读 group）。
2. **owner 标注**：在 2–3 趟旅行的 cull CSV 里填 `scene` 列（细标签），目标每个细标签 ≥ 30 张、没有的标签标 0 张并从默认里删掉；这正是 TASKS.md 未勾的 "cull ground truth" 项。
3. **报告**：`scene_acc`（细）与 `group_acc`（粗）按真值标签分 scope；错得最多的对儿（如 bird_habitat ↔ grassland）决定改提示词还是并标签。
4. **零样本能到多少（文献参考，不是我们的数字）**：
   - CLIP 论文 Table 11，SUN397（397 类场景）零样本 top-1：ViT-B/16 65.2，ViT-L/14-336 68.4；线性探针 78–82。
   - open_clip 结果表：SigLIP（v1）ViT-B-16 约 0.70，SO400M-14-384 约 0.75（第三方复评）。本仓库用的是 `siglip2-base-patch16-224`（`adapters/siglip2.py`），按尺寸对应 B/16 那一档。
   - **SigLIP2 论文、big_vision README 和 open_clip 表都没有 SUN397 / Places 数字**；Places365 零样本对这几个模型没有任何一手数字。
   - 397 类场景零样本约 65–75%，8 组 40 标签应显著高于此但没有可引用的基准，所以 §3 的名单在 owner 标注前一律 **unverified**。
5. **提示词写法**（一手来源）：CLIP `prompts.md` 对 SUN397 只用 `a photo of a {}.` / `a photo of the {}.` 两条；CLIP 论文：默认模板比裸标签 +1.3%（ImageNet），80 模板集成再 +3.5%，集成在**向量空间**求平均（我们的 `text_matrix` 就是这么做的）。
   HF SigLIP2 文档要求 `padding="max_length", max_length=64`（`adapters/siglip2.py:59` 已这样做）并建议 `This is a photo of {label}.`；§3.2 的提示词都是完整句子，符合这个形态。
   建议每标签 2–4 条，描述可见内容（Apple 的原则：visually identifiable，避免节日、职业等抽象词）。

## 5. 需要 owner 决定的事

1. **两级 vs 平级**：默认两级（8 组 → 40 标签）。平级 40 标签会破坏 `per_category`。
2. **`select` 按什么分桶**：默认粗组（8 桶 × 10 = 80 张）；可选按细标签。
3. **night 与 close-up 等是否改为属性**：默认 night **同时**保留为组（`waive` 不变）并新增 `light`/`setting`/`framing` 属性；激进做法是删掉 night 组、只留属性。
4. **消费级杂项要不要**：默认只留一个 `utility`；Google 的 receipts / whiteboards / screenshots / selfies 若要，加到 `other` 组即可，不需要改代码。
5. ~~是否把 `food` 组改名为 `life`~~：已定（2026-09-25）：vehicle / still_life / art 归 `other` 组，`food` 组只有 food_drink。

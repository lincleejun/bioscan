# bioscan → Lightroom Classic（实验）设计

日期：2026-09-24。状态：owner 已批准（对话中），实验性。

## 1. 目标

扫描完一个目录后，一条命令把结果送进 Lightroom Classic：照片自动登记进目录、按分数打 1-5 星、按识别结果打层级关键字、按物种进 collection 分组；没确认到种的不打种名。owner 不需要在 Lightroom 里点任何东西。

成功判据（在 owner 的 Mac 上手动验收）：
```
bioscan run DIR --profile wildlife --json --out preds.ndjson && bioscan lr open preds.ndjson
```
之后 Lightroom 弹到前台，Library 视图切到 `bioscan` collection set，照片带星、带关键字、在对应 collection 里；重跑同一文件不重复、不改 owner 自己打的星。

## 2. 非目标

- 真正的美学分：C2（SigLIP2 aesthetic head）未做，本版用 sharpness 分位数占位。
- 插件调 HTTP 扫描、XMP sidecar 写入、pick/reject、Windows。
- 服务端任何改动：服务仍无状态。

## 3. 分工

| 单元 | 文件 | 职责 |
|---|---|---|
| Python 规则 + CLI | `bioscan/cli/lr.py`，`bioscan/cli/main.py`（子命令），`tests/unit/test_lr.py`，README（中英）Lightroom 一节 | 读 preds.ndjson，算星级/关键字/分组，原子写 `latest.json`，拉起 Lightroom；`lr install` 软链接插件 |
| Lua 插件 | `extensions/lightroom/bioscan.lrplugin/{Info.lua, Apply.lua, Watch.lua, Metadata.lua, dkjson.lua}`，`extensions/lightroom/README.md` | 读 `latest.json`，按路径找/登记照片，写星级、关键字、collection、插件字段；后台监听自动 apply；菜单项手动 apply |

规则全在 Python（pytest 可测），Lua 不含业务判断，也不硬编码任何分组名。

## 4. 契约：`latest.json`（schema 1）

路径（仅 macOS）：`~/Library/Application Support/bioscan/lightroom/latest.json`。Python 先写同目录临时文件再 `os.replace`（原子；监听器不会读到半个文件）。

```json
{
  "schema": 1,
  "run": "2026-09-24T10:00:00.123456+08:00",
  "source": "/abs/preds.ndjson",
  "photos": [
    {
      "path": "/abs/DSC00566.ARW",
      "stars": 4,
      "score": 0.71,
      "keywords": [["bioscan", "bird", "Western Screech-Owl"]],
      "group": "Western Screech-Owl",
      "species": "Megascops kennicottii",
      "level": "species"
    }
  ]
}
```

- `run`：`lr open` 运行时刻的 ISO 8601（带微秒和时区），是幂等键。
- `stars`：整数 0-5；0 = 不碰星级。
- `score`：图内 box 的最高 `quality.sharpness`；无 box 为 0.0。
- `keywords`：层级数组，根在前；插件按顺序 `createKeyword(..., returnExisting=true)` 建链再挂最后一级。
- `group`：collection set `bioscan` 下的 collection 名，Python 决定字符串。
- `species`：分组所用物种的学名，没有为 `""`。`level`：`species | genus | family | unconfirmed | none`（none = 无 box）。

## 5. Python 规则（`bioscan/cli/lr.py`）

只 import 标准库和 `bioscan.contract`（`tests/unit/test_import_light.py` 会查）。用 contract 的 reader（`identify_of`、`boxes_of`、`species_of`、`level_of`、`top_of`）。

输入：NDJSON，每行一个事件；只取 `type == "result"` 的行（error、progress、done、eval 的 meta 行跳过）。

- **score**：`products.aesthetics.score`（run 带 aesthetics stage 且非 null 时；main 于 2026-09-25 合入了 EVA 美学头），否则 `max(box.quality.sharpness)`；无 box 为 0.0。
- **stars**（占位，`# ponytail:` 注释指向 C2）：设 S 为有 box 的照片列表，按 `(score, path)` 升序排，n = len(S)，第 i 个（0 起）得 `1 + (5 * i) // n`，即 1-5 星，每档约 20%；n = 1 得 1 星。无 box 的照片 `stars = 0`。
- **关键字**（每 box 一条，图内去重，保持顺序）：`kind = box.kind`；
  - `level == "species"` 且有 top → `["bioscan", kind, top[0].common or top[0].scientific]`
  - `genus` → `["bioscan", kind, top[0].taxonomy[5]]`；`family` → `["bioscan", kind, top[0].taxonomy[4]]`（taxonomy 不够长则退回下一条规则）
  - 其余（unconfirmed、species 为 None、无 top）→ `["bioscan", kind]`
- **group / species / level**：
  - 有 `level == "species"` 的 box → 其中 `box.score` 最高者：`group = common or scientific`，`species = scientific`，`level = "species"`
  - 否则有 box → `group = "待确认"`，`species = ""`，`level` = box 中最高 `box.score` 者的 level（无 species 时 `"unconfirmed"`）
  - 无 box → `group = "无动物"`，`species = ""`，`level = "none"`
  - 两个中文常量放模块顶部（`REVIEW`、`NONE`）。

CLI：
- `bioscan lr open PREDS [--no-launch] [--to FILE]`：写 `latest.json`（`--to` 改目标，测试用；默认目录不存在则创建），打印写了多少张、几张打星；然后 `open -a "Adobe Lightroom Classic"`（`subprocess.run`），`--no-launch` 或非 darwin 只打印路径。退出码 0；PREDS 不存在或没有 result 行 → stderr 提示，退出码 1。
- `bioscan lr install [--modules DIR] [--copy]`：把 `extensions/lightroom/bioscan.lrplugin` 软链接到 `~/Library/Application Support/Adobe/Lightroom/Modules/`（`--copy` 用复制，SDK 对软链接沉默时的后备）；已存在则说明并退出 0。

测试（`tests/unit/test_lr.py`，合成 NDJSON，写 `tmp_path`，绝不碰真实 home）：stars 公式（n=1、n=5、n=7 含并列）、四种 level 的关键字、图内去重、group 三种情况、error/meta 行跳过、`lr open --no-launch --to` 的输出文件与打印、缺文件退出码。

## 6. Lua 插件

Lightroom Classic 内嵌 **Lua 5.1**：不用 `goto`、`//`、位运算、`table.unpack`。SDK 最低版本 `LrSdkMinimumVersion = 6.0`。`dkjson.lua` 从上游（David Kolf，MIT）原样 vendor，保留许可头，README 记版本。

- `Info.lua`：`LrToolkitIdentifier = "cc.outman.bioscan"`，`LrPluginName = "bioscan"`，`LrInitPlugin = "Watch.lua"`，`LrLibraryMenuItems = {{title = "bioscan: Apply latest scan", file = "Apply.lua"}}`，`LrMetadataProvider = "Metadata.lua"`。
- `Metadata.lua`：插件字段（都存字符串）：`score`、`stars`、`species`、`level`、`run`。
- `Apply.lua`：可 `require` 的 `apply(opts)`，同时作为菜单入口（菜单调用忽略幂等标记）。步骤：
  1. 读 `LrPathUtils.getStandardFilePath("home") .. "/Library/Application Support/bioscan/lightroom/latest.json"`，dkjson 解析；文件不存在或解析失败 → `LrDialogs.message` 一条（只在菜单入口弹；监听器只记日志）。
  2. 每 200 张一个 `catalog:withWriteAccessDo("bioscan apply", fn, {timeout = 30})`，外层 `LrTasks.pcall`，带 `LrProgressScope`。
  3. 每张：`catalog:findPhotoByPath(path)`；nil 且 `LrFileUtils.exists(path)` → `catalog:addPhoto(path)`；不存在 → 计数跳过。
  4. 星级：`r = photo:getRawMetadata("rating")`；`r == nil or r == 0`，或 `tostring(r) == photo:getPropertyForPlugin(_PLUGIN, "stars")`（上次是我们打的）→ `setRawMetadata("rating", stars)`；`stars == 0` 不碰；否则计"跳过已有星"。
  5. 关键字：沿层级 `catalog:createKeyword(name, {}, true, parent, true)`，`photo:addKeyword(leaf)`。
  6. collection：`catalog:createCollectionSet("bioscan", nil, true)` → `createCollection(group, set, true)` → `addPhotos({photo})`。
  7. 插件字段：`setPropertyForPlugin(_PLUGIN, k, tostring(v))`。
  8. 完成：`catalog:setActiveSources({set})`（SDK 不接受 set 就退到第一个 collection），`LrDialogs.showBezel("bioscan: N 张，打星 A，导入 B，跳过 C")`。
- `Watch.lua`：`LrTasks.startAsyncTask`：先 `sleep(5)`，然后每 2 秒查 `LrFileUtils.fileAttributes(path).fileModificationDate`；变化时读文件取 `run`，与 `LrPrefs.prefsForPlugin().lastRun` 不同才 apply，成功后记下。全部 `pcall`，异常写 `LrLogger("bioscan")`（`enable("logfile")`），不弹框。
- `extensions/lightroom/README.md`：安装（`bioscan lr install`，重启 LrC 一次）、日常流程、手动验收清单（10 张 → 检查星/关键字/collection → 重跑不重复 → 手动改星后重跑不被覆盖）、日志位置、Lua 5.1 约束、dkjson 版本。

**As built（2026-09-24）**：六个文件，多一个 `ApplyMenu.lua`（菜单脚本不在 async task 里，且 require 的模块分不清自己是不是菜单入口）；`Info.lua` 加 `LrForceInitPlugin = true`。写权限的分法不是每 200 张一个 gate，而是：先按关键字深度每层一个 gate 建 collection set、collection 和该层关键字（SDK 没承诺同一 gate 里新建的关键字能当父级），然后每 200 张两个 gate（先 addPhoto 缺的，再写元数据），gate 内每张照片各自 `LrTasks.pcall`，一张失败不回滚其余。插件字段 `stars` 只在 bioscan 真写了星级时更新（否则 owner 的星会被当成我们的）。汇总多两项：`失败 N（见日志）`、`未完成`。菜单 apply 成功也记 `lastRun`；监听器只在 apply 成功时记住文件时间，失败约 30 秒后重试。

## 7. 错误处理

见 5、6：缺文件、坏 JSON、路径不存在、addPhoto 失败、写权限超时都只影响那一张或那一次，计数后继续；监听器永不弹框。

## 8. 验证与状态

- 自动：`uv run ruff check .`、`uv run pytest`、`luac -p` 每个 Lua 文件（本机 luac 是 5.4，只能查语法，5.1 约束靠 review）、CI `ci.yml`。
- 真实 Lightroom：2026-09-24 在 owner 的 Mac 上跑通（10 张：登记 10、打星 9、4 个 collection；复跑导入 0）。手改星级不被覆盖、取消/超时路径仍 unverified，步骤在 `extensions/lightroom/README.md`。

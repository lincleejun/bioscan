-- Gardener handoffs (docs/gardener/contract.md, 契约修订 5E): applies each handoffs/<id>.json that has no
-- <id>.result.json yet, then writes that result file with one entry per photo (ok / skipped / failed + reason).
-- Gardener makes every decision except the field-conflict check, which needs Lightroom's current values and
-- follows Decide.lua (the same rules as bioscan/gardener/handoff.py). Develop settings and keywords other than
-- the ones a handoff wrote before are never touched.
local LrApplication = import "LrApplication"
local LrDialogs = import "LrDialogs"
local LrExportSession = import "LrExportSession"
local LrFileUtils = import "LrFileUtils"
local LrFunctionContext = import "LrFunctionContext"
local LrLogger = import "LrLogger"
local LrPathUtils = import "LrPathUtils"
local LrTasks = import "LrTasks"
local json = require "dkjson"
local Decide = require "Decide"

local log = LrLogger("bioscan")
log:enable("logfile")

local Handoff = {}
Handoff.DIR = LrPathUtils.getStandardFilePath("home") .. "/Library/Application Support/bioscan/lightroom/handoffs"

local function resultPath(path)
  return (path:gsub("%.json$", ".result.json"))
end

-- Handoff files still waiting for a result, oldest name first.
function Handoff.pending()
  local out = {}
  if not LrFileUtils.exists(Handoff.DIR) then return out end
  for path in LrFileUtils.files(Handoff.DIR) do
    if path:match("%.json$") and not path:match("%.result%.json$") and not LrFileUtils.exists(resultPath(path)) then
      out[#out + 1] = path
    end
  end
  table.sort(out)
  return out
end

local function write(catalog, name, fn)
  local ok, res = LrTasks.pcall(function()
    return catalog:withWriteAccessDo(name, fn, { timeout = 30 })
  end)
  if ok and res == "executed" then return true end
  log:error("write access failed: " .. tostring(res))
  return false
end

local function join(chain) return table.concat(chain, "|") end

local function chainOf(kw)
  local names = {}
  while kw do
    table.insert(names, 1, kw:getName())
    kw = kw:getParent()
  end
  return table.concat(names, "|")
end

-- The collection set, the handoff's collection and every keyword level, each level in its own gate (as Apply.lua).
local function structure(catalog, doc)
  local s = { kws = {} }
  local depth = 1
  for _, p in ipairs(doc.photos) do
    for _, k in ipairs(p.keywords or {}) do
      if #k.chain > depth then depth = #k.chain end
    end
  end
  for d = 1, depth do
    local ok = write(catalog, "Gardener handoff", function()
      if d == 1 then
        local set = catalog:createCollectionSet(doc.collection_set or "Gardener", nil, true)
        s.col = catalog:createCollection(doc.name, set, true)
      end
      for _, p in ipairs(doc.photos) do
        for _, k in ipairs(p.keywords or {}) do
          if #k.chain >= d then
            local sub = { unpack(k.chain, 1, d) }
            local key = join(sub)
            local parent = d > 1 and s.kws[join({ unpack(k.chain, 1, d - 1) })] or nil
            if not s.kws[key] and (d == 1 or parent) then
              -- synonyms only take effect on a keyword created here; an existing keyword keeps its own
              s.kws[key] = catalog:createKeyword(k.chain[d], d == #k.chain and (k.synonyms or {}) or {}, true, parent, true)
            end
          end
        end
      end
    end)
    if not ok then return nil end
  end
  return s
end

local function find(catalog, p)
  for _, path in ipairs(p.paths or {}) do
    local photo = catalog:findPhotoByPath(path)
    if photo then return photo, path end
  end
  if p.uuid then
    local photo = catalog:findPhotoByUuid(p.uuid)
    if photo then return photo, photo:getRawMetadata("path") end
  end
  return nil
end

local function applyOne(it, s)
  local p, photo = it.p, it.photo
  local r = it.r
  local base = p.baseline or {}
  local current = photo:getRawMetadata("rating")
  local action = Decide.stars(current, base.stars, p.stars or 0)
  if action == "write" then photo:setRawMetadata("rating", (p.stars or 0) > 0 and p.stars or nil) end
  r.stars = { action = action, value = (action == "write" or action == "same") and (p.stars or 0) or current }

  local have, byChain = {}, {}
  for _, kw in ipairs(photo:getRawMetadata("keywords") or {}) do
    local c = chainOf(kw)
    have[#have + 1] = c
    byChain[c] = kw
  end
  local wanted = {}
  for _, k in ipairs(p.keywords or {}) do wanted[#wanted + 1] = join(k.chain) end
  local baseline = nil
  if base.keywords then
    baseline = {}
    for _, c in ipairs(base.keywords) do baseline[#baseline + 1] = c end
  end
  local kaction, add, remove = Decide.keywords(have, baseline, wanted)
  if kaction == "write" then
    for _, c in ipairs(remove) do
      if byChain[c] then photo:removeKeyword(byChain[c]) end
    end
    for _, c in ipairs(add) do
      if not s.kws[c] then error("keyword not created: " .. c) end
      photo:addKeyword(s.kws[c])
    end
  end
  r.keywords = { action = kaction, value = wanted, added = add, removed = remove }
  if s.col then s.col:addPhotos({ photo }) end
  r.status = "ok"
  r.photo_id = photo.localIdentifier
  r.uuid = photo:getRawMetadata("uuid")
end

local function writeJson(final, value)
  local tmp = final .. ".tmp"
  local f = assert(io.open(tmp, "w"))
  f:write(json.encode(value, { indent = true }))
  f:close()
  if LrFileUtils.exists(final) then LrFileUtils.delete(final) end
  LrFileUtils.move(tmp, final)
end

local function writeResult(path, result)
  writeJson(resultPath(path), result)
end

-- ---- 契约修订 7B: develop preset and export step ----
-- Runs after the fields, only on photos that are ok. The decisions follow Decide.lua (handoff.py decide_develop,
-- decide_export). Each photo's result says what really happened; Gardener marks 修图进度 only from it.
-- In the SDK 6.0 reference: developPresetByUuid, developPresetFolders, getDevelopPresets, applyDevelopPreset (in a
-- write gate), getDevelopSettings (in a task; "experimental"), LrExportSession, doExportOnCurrentTask, waitForRender.
-- Unverified until run in Lightroom Classic: getDevelopSettings right after the gate reflecting the preset,
-- loadstring/setfenv on an .lrtemplate, the LR_* export setting names and LR_collisionHandling values.
local function developStep(catalog, doc, items)
  local spec = doc.develop
  if type(spec) ~= "table" or not spec.uuid then return end
  local preset = LrApplication.developPresetByUuid(spec.uuid)
  local todo = {}
  for _, it in ipairs(items) do
    if it.r.status == "ok" then
      local base = (it.p.baseline or {}).develop or {}
      local fp = Decide.fingerprint(it.photo:getDevelopSettings())
      local action = Decide.develop(spec.uuid, base.uuid, base.fingerprint, fp)
      it.r.develop = { action = action, preset = spec.uuid, preset_name = spec.name, fingerprint = fp }
      if action == "conflict" then it.r.develop.reason = "edited_in_lightroom" end
      if action == "apply" then
        if preset then todo[#todo + 1] = it else it.r.develop.action, it.r.develop.reason = "failed", "preset_missing" end
      end
    end
  end
  if #todo == 0 then return end
  local ok = write(catalog, "Gardener develop preset", function()
    for _, it in ipairs(todo) do
      local done, err = LrTasks.pcall(function() it.photo:applyDevelopPreset(preset, _PLUGIN) end)
      if done then it.r.develop.action = "applied" else it.r.develop.action, it.r.develop.reason = "failed", tostring(err) end
    end
  end)
  for _, it in ipairs(todo) do
    if not ok then it.r.develop.action, it.r.develop.reason = "failed", "no write access" end
    if it.r.develop.action == "applied" then it.r.develop.fingerprint = Decide.fingerprint(it.photo:getDevelopSettings()) end
  end
end

local COLLISION = { rename = "rename", skip = "skip", overwrite = "overwrite" }

-- The settings of a saved export preset (an .lrtemplate is a Lua file `s = { ..., value = { settings } }`),
-- sent to the handoff's folder with its overwrite policy. nil + reason when that cannot be done.
local function exportSettings(spec)
  if not COLLISION[spec.overwrite] then return nil, "unknown overwrite policy: " .. tostring(spec.overwrite) end
  local text = spec.preset_path and LrFileUtils.readFile(spec.preset_path)
  if not text then return nil, "export preset not readable: " .. tostring(spec.preset_path) end
  local chunk, err = loadstring(text)
  if not chunk then return nil, "export preset: " .. tostring(err) end
  local env = {}
  setfenv(chunk, env)
  local ran, e = pcall(chunk)
  if not ran or type(env.s) ~= "table" or type(env.s.value) ~= "table" then
    return nil, "export preset: not an export template (" .. tostring(e) .. ")"
  end
  local settings = {}
  for k, v in pairs(env.s.value) do settings[k] = v end
  settings.LR_export_destinationType = "specificFolder"
  settings.LR_export_destinationPathPrefix = spec.dest_dir
  settings.LR_export_useSubfolder = false
  settings.LR_collisionHandling = COLLISION[spec.overwrite]
  return settings
end

local function exportStep(doc, items)
  local spec = doc.export
  if type(spec) ~= "table" then return end
  local photos, byId = {}, {}
  for _, it in ipairs(items) do
    local d = Decide.export(true, it.r.status, it.r.develop and it.r.develop.action)
    if d == "export" then
      photos[#photos + 1] = it.photo
      byId[it.photo.localIdentifier] = it
    elseif d == "skip" then
      it.r.export = { action = "skipped", reason = it.r.status ~= "ok" and "not_in_lightroom" or "develop_failed" }
    end
  end
  if #photos == 0 then return end
  local settings, err = exportSettings(spec)
  if settings then
    local done, e = LrTasks.pcall(function()
      local session = LrExportSession({ photosToExport = photos, exportSettings = settings })
      -- render everything first (blocks this task until done), then read each rendition's real outcome: a
      -- renditions() loop alone might never start rendering outside an export service, and would then wait forever
      session:doExportOnCurrentTask()
      for _, rendition in session:renditions() do
        local success, pathOrMessage = rendition:waitForRender()
        local it = byId[rendition.photo.localIdentifier]
        if it and success then
          it.r.export = { action = "exported", path = pathOrMessage }
        elseif it then
          it.r.export = { action = "failed", reason = tostring(pathOrMessage) }
        end
      end
    end)
    if not done then err = "export failed: " .. tostring(e) end
  end
  for _, it in pairs(byId) do
    if not it.r.export then it.r.export = { action = "failed", reason = err or "no rendition returned" } end
  end
end

-- presets.json next to the handoffs folder: the develop presets and saved export presets Gardener offers.
Handoff.PRESETS = LrPathUtils.parent(Handoff.DIR) .. "/presets.json"
Handoff.EXPORT_PRESETS = LrPathUtils.getStandardFilePath("home") ..
  "/Library/Application Support/Adobe/Lightroom/Export Presets"

function Handoff.writePresets()
  local dev = {}
  for _, folder in ipairs(LrApplication.developPresetFolders()) do
    for _, p in ipairs(folder:getDevelopPresets()) do
      dev[#dev + 1] = { uuid = p:getUuid(), name = p:getName(), folder = folder:getName() }
    end
  end
  local exp = {}
  if LrFileUtils.exists(Handoff.EXPORT_PRESETS) then
    for path in LrFileUtils.recursiveFiles(Handoff.EXPORT_PRESETS) do
      if path:match("%.lrtemplate$") then
        exp[#exp + 1] = { name = LrPathUtils.removeExtension(LrPathUtils.leafName(path)),
          folder = LrPathUtils.leafName(LrPathUtils.parent(path)), path = path }
      end
    end
  end
  table.sort(exp, function(a, b) return a.path < b.path end)
  LrFileUtils.createAllDirectories(LrPathUtils.parent(Handoff.PRESETS))
  writeJson(Handoff.PRESETS, { schema = 1, at = os.date("!%Y-%m-%dT%H:%M:%SZ"), develop = dev, export = exp })
end
-- ---- end 契约修订 7B ----

-- Applies one handoff file. Returns false (and writes no result, so it is retried) when the catalog could not be
-- written at all; per-photo failures are reported in the result file.
function Handoff.applyFile(path)
  local doc = json.decode(LrFileUtils.readFile(path))
  if type(doc) ~= "table" or doc.schema ~= 2 or doc.kind ~= "gardener-handoff" or type(doc.photos) ~= "table" then
    log:error("not a Gardener handoff: " .. path)
    writeResult(path, { schema = 2, id = type(doc) == "table" and doc.id or nil, error = "not a schema 2 Gardener handoff",
      photos = {} })
    return true
  end
  local catalog = LrApplication.activeCatalog()
  local s = structure(catalog, doc)
  if not s then return false end
  local items, toAdd = {}, {}
  for _, p in ipairs(doc.photos) do
    local it = { p = p, r = { asset_id = p.asset_id, status = "failed" } }
    it.photo, it.r.path = find(catalog, p)
    if not it.photo then
      for _, cand in ipairs(p.paths or {}) do
        if LrFileUtils.exists(cand) then it.r.path = cand break end
      end
      if it.r.path then toAdd[#toAdd + 1] = it else it.r.status, it.r.reason = "skipped", "file_missing" end
    end
    items[#items + 1] = it
  end
  if #toAdd > 0 then
    local ok = write(catalog, "Gardener handoff import", function()
      for _, it in ipairs(toAdd) do
        local added, photo = LrTasks.pcall(function() return catalog:addPhoto(it.r.path) end)
        if added and photo then
          it.photo, it.r.imported = photo, true
        else
          it.r.reason = "import failed: " .. tostring(photo)
        end
      end
    end)
    if not ok then
      for _, it in ipairs(toAdd) do it.photo, it.r.reason = nil, "import failed: no write access" end
    end
  end
  local ready = {}
  for _, it in ipairs(items) do if it.photo then ready[#ready + 1] = it end end
  if #ready > 0 then
    local ok = write(catalog, "Gardener handoff apply", function()
      for _, it in ipairs(ready) do
        local done, err = LrTasks.pcall(applyOne, it, s)
        if not done then
          it.r.status, it.r.reason = "failed", tostring(err)
          log:error("handoff apply failed for " .. tostring(it.r.path) .. ": " .. tostring(err))
        end
      end
    end)
    if not ok then
      for _, it in ipairs(ready) do it.r.status, it.r.reason = "failed", "no write access" end
    end
  end
  -- 契约修订 7B: optional steps; an error in one is reported per photo, never lost and never retried forever
  for _, step in ipairs({ { "develop", function() developStep(catalog, doc, items) end },
                          { "export", function() exportStep(doc, items) end } }) do
    local key, fn = step[1], step[2]
    local done, err = LrTasks.pcall(fn)
    if not done and doc[key] then
      log:error("handoff " .. key .. ": " .. tostring(err))
      for _, it in ipairs(items) do
        if it.r.status == "ok" and (not it.r[key] or it.r[key].action == "apply") then
          it.r[key] = { action = "failed", reason = tostring(err) }
        end
      end
    end
  end
  local out, n = {}, { ok = 0, skipped = 0, failed = 0 }
  for _, it in ipairs(items) do
    out[#out + 1] = it.r
    n[it.r.status] = (n[it.r.status] or 0) + 1
  end
  writeResult(path, { schema = 2, id = doc.id, photos = out })
  local msg = string.format("Gardener「%s」: 成功 %d，跳过 %d，失败 %d", doc.name, n.ok, n.skipped, n.failed)
  log:info(msg .. " " .. path)
  LrDialogs.showBezel(msg, 4)
  if s.col then LrTasks.pcall(function() catalog:setActiveSources({ s.col }) end) end
  return true
end

-- Every pending handoff; true when all were applied (a false one is retried by the watcher).
function Handoff.applyPending(opts)
  opts = opts or {}
  local all, todo = true, Handoff.pending()
  if opts.menu and #todo == 0 then LrDialogs.showBezel("Gardener: 没有待处理的交接", 3) end
  for _, path in ipairs(todo) do
    local ok, res = LrTasks.pcall(function()
      return LrFunctionContext.callWithContext("Gardener handoff", function() return Handoff.applyFile(path) end)
    end)
    if not ok then log:error(tostring(res)) end
    all = all and ok and res
  end
  return all
end

return Handoff

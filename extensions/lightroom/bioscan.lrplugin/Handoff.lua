-- Gardener handoffs (docs/gardener/contract.md, 契约修订 5E): applies each handoffs/<id>.json that has no
-- <id>.result.json yet, then writes that result file with one entry per photo (ok / skipped / failed + reason).
-- Gardener makes every decision except the field-conflict check, which needs Lightroom's current values and
-- follows Decide.lua (the same rules as bioscan/gardener/handoff.py). Develop settings and keywords other than
-- the ones a handoff wrote before are never touched.
local LrApplication = import "LrApplication"
local LrDialogs = import "LrDialogs"
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

local function writeResult(path, result)
  local final = resultPath(path)
  local tmp = final .. ".tmp"
  local f = assert(io.open(tmp, "w"))
  f:write(json.encode(result, { indent = true }))
  f:close()
  if LrFileUtils.exists(final) then LrFileUtils.delete(final) end
  LrFileUtils.move(tmp, final)
end

-- Applies one handoff file. Returns false (and writes no result, so it is retried) when the catalog could not be
-- written at all; per-photo failures are reported in the result file.
function Handoff.applyFile(path)
  local doc = json.decode(LrFileUtils.readFile(path), 1, nil, nil, nil)   -- plain tables: the SDK rejects arrays with a metatable ("packed")
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

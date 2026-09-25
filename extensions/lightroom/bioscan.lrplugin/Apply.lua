local LrApplication = import "LrApplication"
local LrDialogs = import "LrDialogs"
local LrFileUtils = import "LrFileUtils"
local LrFunctionContext = import "LrFunctionContext"
local LrLogger = import "LrLogger"
local LrPathUtils = import "LrPathUtils"
local LrPrefs = import "LrPrefs"
local LrProgressScope = import "LrProgressScope"
local LrTasks = import "LrTasks"
local json = require "dkjson"

local log = LrLogger("bioscan")
log:enable("logfile")

local Apply = {}
Apply.PATH = LrPathUtils.getStandardFilePath("home") .. "/Library/Application Support/bioscan/lightroom/latest.json"
local BATCH = 200

function Apply.read()
  if not LrFileUtils.exists(Apply.PATH) then
    return nil, "no scan to apply: " .. Apply.PATH .. " does not exist"
  end
  local doc, _, err = json.decode(LrFileUtils.readFile(Apply.PATH))
  if type(doc) ~= "table" or doc.schema ~= 1 or type(doc.photos) ~= "table" then
    return nil, "cannot read " .. Apply.PATH .. ": " .. tostring(err or "not a schema 1 file")
  end
  return doc
end

-- An error inside withWriteAccessDo rolls back the whole gate, and with a timeout table it
-- returns "aborted" instead of throwing when access is not granted in time.
local function write(catalog, fn)
  local ok, res = LrTasks.pcall(function()
    return catalog:withWriteAccessDo("bioscan apply", fn, { timeout = 30 })
  end)
  if ok and res == "executed" then
    return true
  end
  log:error("write access failed: " .. tostring(res))
  return false
end

local function chainKey(chain, d)
  return table.concat(chain, "\n", 1, d)
end

-- Collection set, collections and keywords are created before any photo is touched: the SDK only
-- promises same-gate use for a keyword/collection a photo is attached to, not for a keyword
-- used as the parent of another new keyword, so each keyword level gets its own gate.
local function createStructure(catalog, photos)
  local s = { cols = {}, kws = {} }
  local depth = 1
  for _, p in ipairs(photos) do
    for _, chain in ipairs(p.keywords or {}) do
      if #chain > depth then depth = #chain end
    end
  end
  for d = 1, depth do
    local ok = write(catalog, function()
      if d == 1 then
        s.set = catalog:createCollectionSet("bioscan", nil, true)
        for _, p in ipairs(photos) do
          if p.group and p.group ~= "" and not s.cols[p.group] then
            s.cols[p.group] = catalog:createCollection(p.group, s.set, true)
            s.first = s.first or s.cols[p.group]
          end
        end
      end
      for _, p in ipairs(photos) do
        for _, chain in ipairs(p.keywords or {}) do
          if #chain >= d then
            local key = chainKey(chain, d)
            local parent = d > 1 and s.kws[chainKey(chain, d - 1)] or nil
            if not s.kws[key] and (d == 1 or parent) then
              s.kws[key] = catalog:createKeyword(chain[d], {}, true, parent, true)
            end
          end
        end
      end
    end)
    if not ok then
      error("could not create the bioscan collections and keywords (see log)")
    end
  end
  return s
end

local function applyOne(it, s, run)
  local p, photo = it.p, it.photo
  local stars = tonumber(p.stars) or 0
  if stars > 0 then
    local r = photo:getRawMetadata("rating")
    if r == nil or r == 0 or tostring(r) == photo:getPropertyForPlugin(_PLUGIN, "stars") then
      photo:setRawMetadata("rating", stars)
      -- "stars" records the rating we wrote; it is left alone when the owner's rating wins.
      photo:setPropertyForPlugin(_PLUGIN, "stars", tostring(stars))
      it.starred = true
    else
      it.kept = true
    end
  end
  for _, chain in ipairs(p.keywords or {}) do
    local kw = s.kws[chainKey(chain, #chain)]
    if kw then photo:addKeyword(kw) end
  end
  local col = p.group and s.cols[p.group]
  if col then col:addPhotos({ photo }) end
  for _, k in ipairs({ "score", "species", "level" }) do
    if p[k] ~= nil then photo:setPropertyForPlugin(_PLUGIN, k, tostring(p[k])) end
  end
  photo:setPropertyForPlugin(_PLUGIN, "run", tostring(run))
end

local function run(context, opts)
  local doc, readErr = Apply.read()
  if not doc then error(readErr, 0) end
  local prefs = LrPrefs.prefsForPlugin()
  if not opts.menu and doc.run == prefs.lastRun then
    return true
  end
  local catalog = LrApplication.activeCatalog()
  local photos = doc.photos
  local n = #photos
  local c = { matched = 0, starred = 0, imported = 0, kept = 0, missing = 0, failed = 0 }
  local complete = true
  local scope = LrProgressScope({ title = "bioscan: applying latest scan", functionContext = context })
  local s = createStructure(catalog, photos)

  for i = 1, n, BATCH do
    if scope:isCanceled() then
      complete = false
      break
    end
    local items, toAdd = {}, {}
    for j = i, math.min(i + BATCH - 1, n) do
      local it = { p = photos[j] }
      it.photo = catalog:findPhotoByPath(it.p.path)
      if it.photo then
        items[#items + 1] = it
      elseif LrFileUtils.exists(it.p.path) then
        items[#items + 1] = it
        toAdd[#toAdd + 1] = it
      else
        c.missing = c.missing + 1
      end
    end

    if #toAdd > 0 then
      local ok = write(catalog, function()
        for _, it in ipairs(toAdd) do
          local added, photo = LrTasks.pcall(function() return catalog:addPhoto(it.p.path) end)
          if added and photo then
            it.photo = photo
          else
            log:error("addPhoto failed for " .. it.p.path .. ": " .. tostring(photo))
          end
        end
      end)
      for _, it in ipairs(toAdd) do
        if not ok then it.photo = nil end
        if it.photo then c.imported = c.imported + 1 else c.failed = c.failed + 1 end
      end
      complete = complete and ok
    end

    local ready = {}
    for _, it in ipairs(items) do
      if it.photo then ready[#ready + 1] = it end
    end
    if #ready > 0 then
      local ok = write(catalog, function()
        for _, it in ipairs(ready) do
          local done, err = LrTasks.pcall(applyOne, it, s, doc.run)
          it.ok = done
          if not done then log:error("apply failed for " .. it.p.path .. ": " .. tostring(err)) end
        end
      end)
      for _, it in ipairs(ready) do
        if ok and it.ok then
          c.matched = c.matched + 1
          if it.starred then c.starred = c.starred + 1 end
          if it.kept then c.kept = c.kept + 1 end
        else
          c.failed = c.failed + 1
        end
      end
      complete = complete and ok
    end
    scope:setPortionComplete(math.min(i + BATCH - 1, n), n)
  end
  scope:done()

  if s.set then
    local shown, err = LrTasks.pcall(function()
      if not catalog:setActiveSources({ s.set }) and s.first then
        catalog:setActiveSources({ s.first })
      end
    end)
    if not shown then log:error("setActiveSources failed: " .. tostring(err)) end
  end

  local msg = string.format("bioscan: %d 张，打星 %d，导入 %d，跳过已有星 %d，磁盘缺失 %d",
    c.matched, c.starred, c.imported, c.kept, c.missing)
  if c.failed > 0 then msg = msg .. string.format("，失败 %d（见日志）", c.failed) end
  if not complete then msg = msg .. "，未完成" end
  log:info(msg .. " run=" .. tostring(doc.run))
  LrDialogs.showBezel(msg, 4)
  if complete then
    prefs.lastRun = doc.run
  end
  return complete
end

-- opts.menu: ignore the lastRun marker and report read errors in a dialog (the watcher only logs).
function Apply.apply(opts)
  opts = opts or {}
  local ok, res = LrTasks.pcall(function()
    return LrFunctionContext.callWithContext("bioscan apply", run, opts)
  end)
  if ok then
    return res
  end
  log:error(tostring(res))
  if opts.menu then
    LrDialogs.message("bioscan", tostring(res), "critical")
  end
  return false
end

return Apply

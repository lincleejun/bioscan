-- Field conflict rules of a Gardener handoff, line for line the same as bioscan/gardener/handoff.py
-- (decide_stars, decide_keywords); tests/gardener/test_core_handoff.py runs both on the same cases.
-- Pure Lua 5.1: no Lightroom imports, so it also runs under a plain `lua`.
local Decide = {}

-- same | none | write | conflict. baseline = the rating Gardener wrote last time (nil = never).
function Decide.stars(current, baseline, wanted)
  current = current or 0
  if wanted == 0 and (baseline == nil or baseline == 0) then
    return "none"
  end
  if wanted == current then
    return "same"
  end
  if baseline == nil then
    if current == 0 then return "write" end
    return "conflict"
  end
  if current == baseline then return "write" end
  return "conflict"
end

local function set(list)
  local s = {}
  for _, v in ipairs(list or {}) do s[v] = true end
  return s
end

local function minus(a, b)
  local out = {}
  for k in pairs(a) do
    if not b[k] then out[#out + 1] = k end
  end
  table.sort(out)
  return out
end

-- (same | write | conflict, chains to add, chains to remove); chains are "Aves|Order|Family|Species".
-- Only chains Gardener wrote (baseline) are ever removed; a baseline chain gone from the photo = changed in Lightroom.
function Decide.keywords(current, baseline, wanted)
  local cur, base, want = set(current), set(baseline), set(wanted)
  if #minus(base, cur) > 0 then
    return "conflict", {}, {}
  end
  local add, remove = minus(want, cur), minus(base, want)
  if #add > 0 or #remove > 0 then return "write", add, remove end
  return "same", add, remove
end

-- 契约修订 7B: develop preset and export step (handoff.py decide_develop, decide_export).
-- none | same | apply | conflict. baseFp = the develop fingerprint right after Gardener's last apply; a different
-- one now = edited in Lightroom since, which a preset would overwrite.
function Decide.develop(wanted, baseUuid, baseFp, currentFp)
  if wanted == nil or wanted == "" then
    return "none"
  end
  if baseFp ~= nil and currentFp ~= baseFp then
    return "conflict"
  end
  if baseUuid == wanted then
    return "same"
  end
  return "apply"
end

-- none | skip | export: only a photo in Lightroom whose wanted preset did not fail.
function Decide.export(wanted, status, developAction)
  if not wanted then
    return "none"
  end
  if status ~= "ok" or developAction == "failed" then
    return "skip"
  end
  return "export"
end

-- A short fingerprint of a develop-settings table (keys sorted, nested tables included), so a later handoff can
-- tell whether the photo was edited in Lightroom after Gardener applied a preset.
local function flatten(t, prefix, out)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = tostring(k) end
  table.sort(keys)
  for _, k in ipairs(keys) do
    local v = t[k]
    if v == nil then v = t[tonumber(k)] end
    if type(v) == "table" then flatten(v, prefix .. k .. ".", out)
    else out[#out + 1] = prefix .. k .. "=" .. tostring(v) end
  end
  return out
end

function Decide.fingerprint(settings)
  local text = table.concat(flatten(settings or {}, "", {}), ";")
  local h = 5381
  for i = 1, #text do
    h = (h * 33 + text:byte(i)) % 4294967296
  end
  return string.format("%08x-%d", h, #text)
end

-- An export preset's settings from the text of its .lrtemplate (a Lua file `s = { ..., value = { settings } }`).
-- Lightroom's Lua has no setfenv, so the file is read as an expression: `s =` becomes `return`. Only the built-in
-- "Hard Drive" export is accepted; a preset of another plug-in's export service would run that plug-in instead.
-- Returns the settings table, or nil and the reason.
Decide.FILE_EXPORT = "com.adobe.ag.export.file"
function Decide.templateSettings(text)
  local body, n = (text or ""):gsub("^%s*s%s*=%s*", "return ", 1)
  if n ~= 1 then return nil, "not an export template" end
  local chunk, err = (loadstring or load)(body)   -- Lightroom: Lua 5.1 loadstring; tests: 5.4 load
  if not chunk then return nil, "export preset: " .. tostring(err) end
  local ran, s = pcall(chunk)
  if not ran or type(s) ~= "table" or type(s.value) ~= "table" then return nil, "not an export template" end
  local service = s.value.exportServiceProvider
  if service ~= nil and service ~= Decide.FILE_EXPORT then
    return nil, "export preset uses another plug-in (" .. tostring(s.value.exportServiceProviderTitle or service) ..
      "); choose a Hard Drive export preset"
  end
  local settings = {}
  for k, v in pairs(s.value) do settings[k] = v end
  return settings
end

return Decide

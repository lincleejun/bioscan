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

return Decide

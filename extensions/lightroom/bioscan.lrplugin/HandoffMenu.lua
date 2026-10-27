local LrTasks = import "LrTasks"
local Handoff = require "Handoff"

-- Applies every Gardener handoff that has no result yet (the watcher does the same every few seconds).
LrTasks.startAsyncTask(function()
  Handoff.applyPending({ menu = true })
end, "Gardener handoff")

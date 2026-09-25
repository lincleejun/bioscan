local LrFileUtils = import "LrFileUtils"
local LrLogger = import "LrLogger"
local LrTasks = import "LrTasks"
local Apply = require "Apply"

local log = LrLogger("bioscan")
log:enable("logfile")

-- Polls latest.json; Apply.apply skips a file whose run equals prefs.lastRun and records it on success.
-- A Plug-in Manager "Reload" starts a second loop until Lightroom restarts; applies are idempotent.
LrTasks.startAsyncTask(function()
  LrTasks.sleep(5)
  local seen
  while true do
    local ok, err = LrTasks.pcall(function()
      local mtime = LrFileUtils.fileAttributes(Apply.PATH).fileModificationDate
      if mtime and mtime ~= seen then
        seen = mtime
        Apply.apply({})
      end
    end)
    if not ok then log:error("watcher: " .. tostring(err)) end
    LrTasks.sleep(2)
  end
end, "bioscan watcher")

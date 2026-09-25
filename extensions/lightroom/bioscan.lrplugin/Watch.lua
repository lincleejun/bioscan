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
        -- seen is recorded only on success, so a failed apply (write-access timeout at startup,
        -- a batch failure) is retried; the longer sleep keeps a bad file from spinning the log.
        if Apply.apply({}) then seen = mtime else LrTasks.sleep(28) end
      end
    end)
    if not ok then log:error("watcher: " .. tostring(err)) end
    LrTasks.sleep(2)
  end
end, "bioscan watcher")

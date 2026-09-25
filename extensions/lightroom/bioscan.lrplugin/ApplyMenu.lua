local LrTasks = import "LrTasks"
local Apply = require "Apply"

-- Menu scripts do not run in an async task; catalog calls need one.
LrTasks.startAsyncTask(function()
  Apply.apply({ menu = true })
end, "bioscan apply")

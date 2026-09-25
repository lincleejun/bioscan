return {
  LrSdkVersion = 6.0,
  LrSdkMinimumVersion = 6.0,
  LrToolkitIdentifier = "cc.outman.bioscan",
  LrPluginName = "bioscan",
  LrInitPlugin = "Watch.lua",
  LrForceInitPlugin = true,
  LrMetadataProvider = "Metadata.lua",
  -- The menu script is a separate file: a required module cannot tell whether it runs as a menu item.
  LrLibraryMenuItems = {
    { title = "bioscan: Apply latest scan", file = "ApplyMenu.lua" },
  },
  VERSION = { major = 0, minor = 1, revision = 0 },
}

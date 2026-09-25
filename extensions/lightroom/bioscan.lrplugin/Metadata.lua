-- All values are strings (setPropertyForPlugin takes a string). readOnly keeps the owner from
-- editing "stars" by hand, which would break the star ownership check in Apply.lua.
return {
  schemaVersion = 1,
  metadataFieldsForPhotos = {
    { id = "species", title = "bioscan species", dataType = "string", readOnly = true, searchable = true, browsable = true },
    { id = "level", title = "bioscan level", dataType = "string", readOnly = true, searchable = true, browsable = true },
    { id = "score", title = "bioscan score", dataType = "string", readOnly = true },
    { id = "stars", title = "bioscan stars", dataType = "string", readOnly = true },
    { id = "run", title = "bioscan run", dataType = "string", readOnly = true },
  },
}

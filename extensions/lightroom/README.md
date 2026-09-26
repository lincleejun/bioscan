# bioscan for Lightroom Classic (experimental, macOS)

A Lightroom Classic plug-in that takes a bioscan run and puts it into your catalog: it adds the photos,
gives them 1-5 stars, adds hierarchical keywords (`bioscan > bird > Western Screech-Owl`), files them
into collections under a `bioscan` collection set, and stores the score, species and level in plug-in
metadata fields. All the rules live in Python (`bioscan lr open`); the plug-in only applies
`latest.json`. Design: `docs/superpowers/specs/2026-09-24-lightroom-plugin-design.md`.

Status: **run once in Lightroom Classic on the owner's Mac (2026-09-24, 14.x):** symlinked plug-in loaded from
Modules, the watcher applied a 10-photo run at startup (10 added, 9 starred, 4 collections), a rerun of the same
photos added nothing. Checklist steps 5 (a hand-changed star is kept) and the cancel/timeout paths are still unverified.

## Install

```
bioscan lr install
```

This symlinks `extensions/lightroom/bioscan.lrplugin` into
`~/Library/Application Support/Adobe/Lightroom/Modules/`. Restart Lightroom Classic once. Plug-ins in
the Modules folder are loaded automatically, so there is no Plug-in Manager "Add" step; the plug-in
shows up in File > Plug-in Manager, where you can disable it. If Lightroom does not list it (it may not
follow a symlinked `.lrplugin`), run `bioscan lr install --copy` and restart again; a copy does not
follow plug-in updates, so delete it from Modules and rerun `--copy` after pulling changes.

## Daily flow

```
bioscan run DIR --profile wildlife --json --out preds.ndjson && bioscan lr open preds.ndjson
```

`lr open` writes `~/Library/Application Support/bioscan/lightroom/latest.json` and brings Lightroom to
the front. The plug-in checks that file every 2 seconds (starting 5 seconds after Lightroom starts) and
applies each new `run` once. When it is done the Library shows the `bioscan` collection set and a
short message gives the counts: photos matched, starred, imported, skipped because they already had
your stars, and missing on disk (plus failures, if any).

To apply the same file again by hand: Library > Plug-in Extras > **bioscan: Apply latest scan**. The
menu item always applies, even if that run was applied already. A run that ends in `未完成` (a batch timed out, failed or was
cancelled) is retried by the watcher about 30 seconds later; the menu item does it now.

What it does to each photo:

- **Import.** A path not in the catalog is added in place ("Add", no copy, no presets) if the file
  exists; otherwise it is counted as missing on disk.
- **Stars.** `stars` 1-5 is written only if the photo has no stars, or still has the stars bioscan
  wrote last time (kept in the `bioscan stars` field). Stars you set by hand are not overwritten (unless you happen to set exactly the value bioscan wrote).
  `stars = 0` (no animal) leaves the rating alone.
  A rating you cleared to 0 by hand counts as unrated, so the next `lr open` stars that photo again.
- **Keywords.** Each `keywords` entry is a path, root first; missing levels are created, the photo gets
  the last one. Keywords are only added, never removed.
- **Collections.** The photo is added to the collection named `group` inside the `bioscan` set.
  Photos are never removed from collections, so a photo whose group changed between runs is in both.
- **Fields.** `bioscan species`, `bioscan level`, `bioscan score`, `bioscan stars`, `bioscan run` in
  the Metadata panel (read-only; species and level are searchable, e.g. for smart collections).

## Acceptance checklist (manual, on the owner's Mac)

1. Pick a folder with about 10 photos, some with animals and one without, and one already rated by hand.
   Run the daily-flow command.
2. Within a few seconds: the Library shows the `bioscan` set; every photo is in the catalog; photos
   with animals have 1-5 stars; the hand-rated one kept its stars; the no-animal photo has no new stars
   and is in the `无动物` collection.
3. Keywords panel: `bioscan > <kind> > <species>` (`<English> (<Latin>)` at genus or family, `bioscan > <kind>` for unconfirmed). Species
   collections contain the right photos. The Metadata panel shows the five `bioscan` fields.
4. Run `bioscan lr open preds.ndjson` again: no duplicate photos, keywords or collections; star counts
   unchanged.
5. Change the stars of one bioscan-starred photo by hand, run `lr open` again (or the menu item): that
   photo keeps your stars and the message counts it under "skipped".

## Log

Errors and one line per apply go to the `bioscan` logger with the `logfile` action:
`~/Library/Logs/Adobe/Lightroom/LrClassicLogs/bioscan.log` (Lightroom Classic 14 on macOS; the SDK guide's
`~/Documents/bioscan.log` is where older versions put it). The watcher never shows a
dialog; only the menu item reports a missing or unreadable `latest.json` in a dialog.

## Lua constraints

Lightroom Classic embeds Lua 5.1 (the compiled plug-ins in the owner's Modules folder are Lua 5.1
bytecode). Do not use `goto`, `//`, bitwise operators, `table.unpack` (use `unpack`),
`math.tointeger` or `utf8`. The only automated check is `luac -p *.lua` with the local Lua 5.4 `luac`,
which catches syntax errors but not 5.4-only features, so review for those by hand.

Files: `Info.lua` (manifest), `Metadata.lua` (fields), `Apply.lua` (module doing the work),
`ApplyMenu.lua` (menu item: starts an async task and calls `Apply.apply`), `Watch.lua` (init script
that polls `latest.json`), `dkjson.lua`.

## dkjson

`dkjson.lua` is David Kolf's JSON module, **version 2.11**, vendored unchanged from
<http://dkolf.de/dkjson-lua/dkjson-2.11.lua> (MIT licence, header kept in the file). It runs on
Lua 5.1 without LPeg; the plug-in never calls `use_lpeg`. Its load-time `require "debug"` is inside a
`pcall`, so Lightroom's plug-in-only `require` failing there is harmless.

## SDK assumptions

Checked against the Lightroom SDK 6.0 API reference (a copy of Adobe's HTML reference, e.g.
`LrCatalog.html` in <https://github.com/micdah/LrControl/tree/master/Docs/Lightroom%20SDK%206.0/API%20Reference/modules>)
and the Lightroom Classic SDK Guide 2020
(<https://ioconsolerykerprodcdn.azureedge.net/static/installers/lr/sdk/2020/doc/Lightroom%20Classic%20SDK%20Guide%202020.pdf>).

Verified in Lightroom Classic 14 (macOS): a symlinked `.lrplugin` in Modules loads; `LrInitPlugin` starts the
watcher; `addPhoto`, ratings, keyword chains, collections, plug-in fields and `setActiveSources({set})` all worked.

Verified in the documentation:

- `catalog:setActiveSources(sources)` accepts an array mixing `LrCollection` and `LrCollectionSet` and
  returns false on bad sources; the plug-in passes `{set}` and falls back to the first collection on false.
- `photo:setPropertyForPlugin` takes a string value that must match the field's `dataType`; fields are
  declared `dataType = "string"` and every value goes through `tostring`.
- `withWriteAccessDo(name, fn, {timeout = 30})` returns `"executed"` or `"aborted"` (not an error) when
  write access is not granted in time; an error inside `fn` rolls back the whole gate. Each photo is
  therefore wrapped in its own `LrTasks.pcall` inside the gate.
- `createKeyword(name, synonyms, includeOnExport, parent, returnExisting)`,
  `createCollectionSet(name, parent, canReturnPrior)`, `createCollection(name, parent, canReturnPrior)`,
  `addPhoto(path)` ("Add" without moving, inside a write gate), `findPhotoByPath(path)` (async task,
  nil if absent), `LrCollection:addPhotos` and `photo:addKeyword` may be used in the gate that created
  the collection/keyword, a collection may be created inside a set created in the same gate.
- `LrFileUtils.fileAttributes(path).fileModificationDate` (empty table if missing), `LrFileUtils.exists`,
  `LrFileUtils.readFile`, `LrDialogs.showBezel(message, fadeDelay)` (SDK 5.0+), `LrPrefs.prefsForPlugin()`
  (stores strings), `LrTasks.pcall/sleep/startAsyncTask`, `LrProgressScope{title, functionContext}` with
  `isCanceled`/`setPortionComplete`/`done`, `LrPathUtils.getStandardFilePath("home")`.
- Info.lua keys `LrInitPlugin`, `LrForceInitPlugin`, `LrMetadataProvider`, `LrLibraryMenuItems`; metadata
  provider keys `schemaVersion`, `metadataFieldsForPhotos`, `title`, `dataType`, `readOnly`, `searchable`,
  `browsable`; plug-ins in `~/Library/Application Support/Adobe/Lightroom/Modules` load automatically;
  `require "Name"` loads `Name.lua` from the plug-in folder and returns the chunk's value.

Unverified (not stated in the docs, or only testable in Lightroom):

- Using a keyword created earlier in the same gate as the parent of a new keyword. The plug-in avoids
  relying on it: each keyword level is created in its own gate before any photo is touched.
- Whether a gate stays usable after an error is caught with `LrTasks.pcall` inside it (per-photo
  failures such as a failed `addPhoto`).
- A Plug-in Manager "Reload" starts a second watcher loop until Lightroom restarts; applies are
  idempotent, so this only costs a little polling.

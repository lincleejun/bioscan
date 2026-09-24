# TASKS — v1.5 W5: RAW input robustness

Base: 0a66f72 (branch `v15/w5-raw`). Source of truth for this work package.

## Setup / evidence
- [x] baseline: ruff clean, pytest 160 passed / 8 skipped on base
- [x] problems 1-3 reproduced on base (golden "before"): CR3/RAF/ORF/RW2 -> (None, None, None); SubSecTimeOriginal
      ignored; CR2/NRW/ORF/PEF/RW2/SRW not scanned by `run` or `gt folders`
- [x] golden "before" recorded from a clean `git archive 0a66f72` (read_exif over 76 inputs, decode over 128 files
      with rawpy faked, 4 detail cases, 4 folder scans)

## 1. EXIF/GPS/time for every supported RAW (stdlib + Pillow)
- [x] TIFF-variant headers (ORF IIRO/IIRS/MMOR, RW2 IIU) parsed as TIFF; RW2 JpgFromRaw fallback
- [x] CR3: ISOBMFF moov -> Canon uuid -> CMT1/CMT2/CMT4 (32- and 64-bit box sizes)
- [x] RAF: embedded JPEG from the header (offset/length at 84)
- [x] HEIC/HEIF: unsupported, documented (decode itself needs pillow-heif, a new dependency)
- [x] rawpy check: `raw.other` has iso/shutter/aperture/focal/timestamp/shot_order/artist; no GPS, no offset, no
      sub-seconds -> not used
- [x] tests: synthetic fixture per container (tests/unit/raw_fixtures.py) + 17 broken inputs -> (None, None, None)
- [x] real-file check: `uv run python -m bioscan.service.decode DIR -r`

## 2. SubSecTimeOriginal
- [x] fraction appended (`2026-05-01T08:00:00.37-07:00`); DateTime fallback pairs with SubSecTime; gt (exiftool) agrees
- [x] consumers: geo.week_of reads [5:7]/[8:10] (test); eval passes GT taken_at through; render and the CLI do not
      read it; not in the result event; datetime.fromisoformat parses the fraction

## 3. One shared extension list
- [x] `bioscan/formats.py` (stdlib): RAW_EXT, SCAN_EXT, DEFAULT_EXT, parse_ext, list_images, is_raw, subsec
- [x] decode, `bioscan run`, `bioscan gt folders` use it; import-light test covers it
- [x] test: lower, UPPER and Capitalised file of every extension scanned by both commands

## 4. Behaviour preserved
- [x] golden after vs before: 144 identical, 73 differ, all intended (new containers' lat/lon/taken_at, sub-second
      fraction, added scan extensions); pixels, sizes, orientation, sha256 identical everywhere

## 5. Docs
- [x] README + README.zh-CN supported-files table, check command, sub-seconds; layout lines
- [x] CONTEXT.md: Scan extensions, Container, Capture time
- [x] design spec `--ext` default

## Wrap-up
- [x] ruff + pytest green (200 passed, 8 skipped)
- [x] self-review of diff against base
- [ ] owner: run the check command over real CR3 / RAF / ORF / RW2 (and ARW / NEF) files on the Mac
- [ ] CI (ci.yml, models.yml) on the pushed head: not pushed by this package

## Review notes (orchestrator, after acceptance)
- [x] ORF/RW2: IFDs parsed from the first 4 MB (no whole-file copy); JpgFromRaw sliced from the file by offset/count
- [x] CR3: each CMT block parsed on its own; a corrupt one no longer loses the others
- [x] gps_from_ifd: None for non-finite (0/0 rationals gave NaN on base) or out-of-range (|lat| > 90, |lon| > 180)
- [x] formats.subsec decodes bytes as ASCII (NULs dropped; non-ASCII -> no fraction)
- [x] golden re-run: 152 identical, 77 differ, all intended (adds cr3_bad_cmt1, rw2_far_preview, gpsbad_*)

## Found
- `gt folders --ext .arw` matched nothing (no dot stripping, unlike `run`); both now share `formats.parse_ext`.
- `gt.read_exif` (own-tier CSV, `run --lat` default) uses exiftool, which already reads every format; left as is.

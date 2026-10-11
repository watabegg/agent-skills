---
name: bass-tab-from-photos
description: Transcribe user-provided band-score photos or scanned bass notation into pitch-checked, repeat-unfolded MuseScore bass TAB and PDF; use for bass TAB creation/correction/layout from score images.
---

# Bass TAB Transcription from Score Photos

## Purpose and Operational Scope

This skill provides a reusable transcription and verification workflow developed from a real transcription session where numerous pitches were rendered incorrectly due to unreliable staff and TAB line registration.

The workflow processes user-provided band-score photos or scanned notation containing bass parts to produce pitch-verified, repeat-unfolded MuseScore bass TAB files and rendered PDFs. It is not designed for generic audio transcription, nor for vocal or chorus extraction. Never treat previously generated transcriptions as ground truth. No claim of complete optical music recognition (OMR) accuracy or full-song reproducibility has been proven. Always preserve the user's defined transcription scope, instrument tuning, and layout choices.

## Workflow Execution Sequence

Execute the following sequential process when converting score images into verified notation:

1. **Inventory Source Material**: Catalog all image files, check page numbers, logical reading order, and page orientation. Record source identifiers down to individual pages, systems, and source measure indices.
2. **Catalog Notation Parameters**: Record actual clefs, clef changes, key signatures, alternate tunings, capos, and transposition rules present in the source.
3. **Audit Environment Tools**: Discover the installed MuseScore executable or AppImage and check CLI capabilities without hardcoding versions.
4. **Inspect Source Images**: Use image viewers for genuine visual interpretation. Use programmatic tools such as Pillow or OpenCV strictly for cropping, rotation, and geometric rectification—never for generative image modification. Do not mandate third-party OMR installations; if an optional recognition pass is proposed, every element requires visual corroboration against the source images.
5. **Extract Events into Source Ledger**: Transcribe events into a structured, source-indexed ledger, verifying pitches by triangulating TAB fret numbers, standard notation, and harmonic context.
6. **Build, Verify, and Layout**: Build MusicXML using the helper script, compile to MuseScore, run native verification against the ledger, apply structural layout, and render the final PDF.

## Score Geometry and Staff Registration Safeguards

Band scores arrange vocal, guitar, bass, and drum staves in dense vertical configurations. Lens distortion, camera curvature, and book binding tilt cause vertical coordinates ($y$) to vary locally across each system. Automated line detectors routinely latch onto beam lines, drum staves, or adjacent guitar staves one line away, producing lines that appear flat and plausible but introduce severe systematic pitch errors.

Enforce the following geometric safeguards:
- Locate the explicit bass staff label on every page. Identify both the standard notation staff and the 4-line TAB staff.
- Ensure bounding crops capture all four TAB lines, chord symbols above the staff, and adjacent barlines. Do not crop by fixed page percentages across a multi-page book.
- Visually verify staff geometry for every single system at representative left, center, and right points.
- Overlay the actual 4 TAB lines or 5 standard notation lines directly onto the original source image before accepting a crop or coordinate system.
- Anchor coordinates manually if automatic line fitting is uncertain. Never apply a global vertical offset to compensate for local curvature or tilt.

## Pitch Verification and Harmonic Triangulation

Electric bass TAB requires independent reading of fret numerals and string line assignments:

- **Tuning Baseline**: Standard 4-string electric bass has sounding open MIDI pitches of E1 = 28, A1 = 33, D2 = 38, and G2 = 43, ordered top-to-bottom on the TAB staff as G, D, A, and E. Confirm the instrument's actual tuning from score markings instead of imposing standard tuning on alternate setups.
- **Glyph and Line Decoupling**: Inspect both the fret numeral glyph and the specific horizontal line crossing through it. Misidentifying an adjacent string often shifts the sounding pitch by 5 semitones, while misreading a fret digit or line placement can also produce pitch errors, including shifts of 1 to 2 semitones.
- **Standard Notation Cross-Reading**: Record both the TAB-derived sounding MIDI pitch and an independently derived standard-staff sounding MIDI pitch (using actual clefs, key signatures, accidentals, and ledger lines local to that bar) for each event, requiring exact equality before acceptance or flagging the note as unresolved. Electric bass standard notation is conventionally written one octave above sounding pitch (+12 semitones) and transposed down (-12 semitones) for bass playback; do not apply a double-transposition. Track per-note coordinates against actual local line positions and same-string neighbors. Where no standard staff is available, perform a second note-by-note string assignment from original local line anchors and record that evidence is limited, never claiming independent pitch corroboration.
- **Harmonic Triangulation**: Cross-reference ambiguous fret readings against chord root, third, and fifth, standard notation pitches, repeating motifs, and voice-leading conventions. Non-chord tones (passing tones, suspensions, pedal points, chromatic approach notes) are legitimate musical figures; use chord symbols to diagnose suspicious entries, but never overwrite a clearly legible source note to match the nearest chord tone.

## Event Ledger and Ambiguity Resolution

Maintain a structured, source-indexed event ledger outside the public skill repository:

- Record event durations as exact rational quarter-beat fractions (e.g., quarter note = 1, eighth note = 1/2, sixteenth note = 1/4).
- Track explicit string index, fret number, rests, dead/ghost notes, chord annotations, and tie statuses.
- Keep source measure numbers strictly separate from expanded performance measure positions.
- In the private ledger, record original crop coordinates or asset identifiers, geometric anchors, chord symbols, parsed events, corroborating evidence, and uncertainty flags.
- Resolve uncertain notes before making a final accuracy claim. When a passage is genuinely illegible, document and report the remaining unreadable notes instead of silently guessing, and prompt the user with targeted questions referencing specific source coordinates.

## Form Unfolding, Repeats, and Rhythmic Arithmetic

Expand physical notation into a linear performance sequence:

- **Unfold Explicit Repeats**: Trace repeat barlines, first- and second-time endings, ditto marks (measure repeats), Dal Segno (D.S.), and Coda markings into a fully unfolded performance order. Be aware that small cue or guide notes may indicate alternate passes across repetitions rather than simultaneous polyphonic harmonies; evaluate their actual function from the notation and musical context instead of assuming either interpretation by default.
- **Ties vs. Articulations**: Apply ties strictly between consecutive events that share the identical sounding pitch. Distinguish ties from slurs, hammer-ons, pull-offs, and slides (glissandi).
- **Rhythmic Arithmetic**: Ensure the sum of event durations in every measure strictly equals the time signature. Calculate eighth-note triplets as 1/3 beat and sixteenth-note triplets as 1/6 beat.
- **Tempo Markings**: If numeric metronome marks are absent from the score, consult authorized local recordings or musical research. Distinguish meter and tempo aliases such as 3:2 feels or half-time and double-time shifts. Never label an estimated BPM as confirmed. When the task requires only pitch transcription and score engraving, tempo research is not required unless audio playback is explicitly requested.

## Helper Tooling ([`score.py`](scripts/score.py))

A standard library Python CLI script (`scripts/score.py`) is provided to handle score generation and validation without external Python dependencies. Resolve script paths relative to the skill directory rather than the current working directory. Refer to [format.md](references/format.md) for data schema definitions.

Available commands:
- `python3 scripts/score.py build INPUT.json OUTPUT.musicxml`: Converts the validated JSON ledger into structured MusicXML.
- `python3 scripts/score.py verify INPUT.json SCORE.mscz`: Compares the compiled MuseScore archive against the independent source ledger, verifying native sounding pitches, string assignments, frets, rational durations, and bar identity.
- `python3 scripts/score.py layout INPUT.json SCORE.mscz OUTPUT.mscz`: Applies system break configurations and layout spacing to the score.

The helper supports standard 4-string bass or custom low-to-high tunings. Pitch is computed from string and fret assignments, written +12 with transpose -12 for bass. The helper does not perform image recognition, infer repeat structures, prove transcription fidelity against source photos, or support arbitrary notation outside its schema. Run `verify` only after completing source-level visual verification; native verification confirms fidelity to the ledger, but does not validate against the photo source itself.

## Headless MuseScore Execution and Environment Handling

Execute MuseScore headless operations under Linux with attention to CLI argument ordering and filesystem limits:

- **Binary Discovery**: Dynamically locate the installed MuseScore executable or AppImage rather than relying on hardcoded paths or versions.
- **Filesystem Caching**: Redirect temporary files by specifying `TMPDIR=<cache>` in a writable local workspace directory when `/tmp` has storage quota restrictions.
- **CLI Invocation and Environment Options**: On Linux, CLI behavior varies across MuseScore versions. Prefer setting `QT_QPA_PLATFORM=offscreen` in the environment without the `-platform` flag, or use the version-dependent fallback of placing the input file path prior to `-platform offscreen` if argument parsing requires it (certain versions, such as 4.7.5, may emit additional-input warnings while still generating valid outputs). Discover version-specific behavior and validate actual output files (the commands below illustrate the input-first fallback):
  ```bash
  TMPDIR=<cache> <musescore_bin> <input.musicxml> -platform offscreen -o <score.mscz>
  python3 scripts/score.py verify <input.json> <score.mscz>
  python3 scripts/score.py layout <input.json> <score.mscz> <layout.mscz>
  TMPDIR=<cache> <musescore_bin> <layout.mscz> -platform offscreen -o <output.pdf>
  ```
- **Validation Checks**: An exit code of 0 does not confirm rendering success. Inspect the resulting `.mscz` by unzipping the archive and parsing the internal XML, verify the output file exists, confirm a non-zero page count in the rendered PDF, and visually inspect the output. Identical engraving output across different MuseScore versions is not guaranteed.
- **Filesystem Transfers**: When copying outputs to Google Drive or FUSE-mounted directories, use `shutil.copyfile` instead of `shutil.copy2` to prevent extended attribute (`xattr`) errors, and compute file hashes after writing to verify data integrity.
- **Optional Audio Exports**: If audio playback is explicitly requested, select an appropriate sound profile and export audio. Verify that decoded audio frames are finite, non-empty, and match the exact score duration without digital clipping. Pipeline export success confirms file generation, not musical correctness.

## Score Layout and Visual Engraving Review

When matching standard band-score engraving styles, apply these layout conventions:

- Suppress long and short instrument names to keep systems clean.
- Position rehearsal marks at the beginning of systems where practical.
- Target an average density of approximately 4 bars per system.
- For short concluding measure groups, insert a native horizontal box (frame) rather than padding the score with blank rest bars.
- In triplet or mixed-meter passages, group measures according to guitar and bass phrasing and meter rather than forcing mechanical 4-bar blocks (for example, grouping 4/4 + 4/4 + 2/4 into a single 3-bar phrase).
- Treat layout defaults as user preferences, not rigid global rules. Configure explicit systems of performance measure indices and optional `spacer_slots` in the layout JSON.
- Render all pages of the PDF and inspect for clipped fret digits, beam and tuplet collisions, broken ties, misaligned labels, inconvenient page turns, and short systems.
- Verify that layout modifications do not alter underlying pitches, rhythms, or repeat order.

## Delivery Standards and Repository Cleanliness

Deliver completed work with strict separation between public and private assets:

- **Deliverables**: Provide the scoped `.mscz` score file and the rendered `.pdf`.
- **Accompanying Summary**: Provide a concise summary documenting what was transcribed, verified, and corrected, accompanied by an explicit description of any remaining legibility limits.
- **Preservation**: Retain original source image files unmodified; update or replace only authorized project deliverables.
- **Repository Hygiene**: Keep private source scans, audio recordings, crop coordinates, and extraction ledgers outside the public skill repository. Never commit copyrighted score images, song titles, artist names, or hardcoded personal directory paths into repository code.
- **Operational Boundaries**: Standard transcription workflows do not require blanket interactive approval dialogs or external subagent invocations. Independent model evaluations are decoupled from this workflow and remain strictly optional.

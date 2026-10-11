# Bass TAB Ledger Reference Format (`format.md`)

This document specifies the ledger JSON schema and standard CLI pipeline for [`scripts/score.py`](../scripts/score.py) in the bass TAB skill, linking back to [SKILL.md](../SKILL.md). Reliable bass transcription requires deterministic sounding pitch calculation and rigid single-voice rhythmic parsing without depending on heuristic engraving deductions. The ledger serves as an explicit intermediate representation that unrolls performance order and enforces exact rational timing. This document covers schema and execution rules; it is not environment installation documentation.

## Root Schema and Measures

The root ledger object defines score-level attributes, instrument tuning, and source measure definitions:

- `title` *(optional string)*: Title of the score, defaulting to `"Bass TAB"`.
- `tuning` *(optional array of exactly 4 integers)*: Low-to-high sounding MIDI pitches, defaulting to standard 4-string bass `[28, 33, 38, 43]`. Custom four-string tunings are supported; arbitrary clef or octave transpositions are not. Sounding pitch is computed as `tuning[4 - string] + fret`. Standard bass notation is written an octave higher (+12) with a `-12` playback transposition. Note spellings use canonical sharps for MusicXML helpers; source accidentals are inferred into fret numbers before building.
- `measures` *(required array)*: List of unique source measure definitions. All bars are strictly monophonic or chordal within a single rhythm voice.
  - `id` *(required string)*: Unique identifier for the measure.
  - `meter` *(array `[int, int]`)*: Meter signature, defaulting to `[4, 4]`. Event durations within the measure must sum exactly to this meter.
  - `key` *(integer `-7..7`)*: Key signature in fifths, defaulting to `0`. Key signatures are not inherited across measures; the correct key must be declared in every source measure.
  - `tempo` *(optional number)*: Quarter-note BPM, repeated on every traversal when source measures unfold.
  - `label` *(optional string)*: Rehearsal label, repeated on every traversal.
  - `events` *(required array)*: Ordered rhythmic events.
    - `duration` *(integer or string fraction)*: Exact rational quarter-beats (`4` whole, `2` half, `1` quarter, `"1/2"` eighth, `"1/4"` 16th, `"1/8"` 32nd, dotted variants, or triplets `"2/3"` quarter, `"1/3"` eighth, `"1/6"` 16th). Floating-point values are rejected.
    - `notes` *(optional array)*: Sounding notes; an empty array (`[]`) denotes a rest.
      - `string` *(integer `1..4`)*: String index from `1` (highest, G) to `4` (lowest, E) in standard tuning.
      - `fret` *(integer `0..36`)*: Fret position.
      - `dead` *(optional boolean)*: Dead note flag, which requires `fret: 0`.
    - `tie` *(optional boolean)*: Sustains all pitches into the immediately following performance event without intervening rests, dead notes, or different pitches. Ties apply across repeat jumps on every traversal; variant measures must be defined when passes differ. Ties must not be used to fake slurs, slides, bends, hammer-ons, or articulations.

Automatic OMR, repeat discovery, independent polyphony, grace notes, slurs, slides, bends, hammer-ons, and articulations are not supported. Handle these separately in MuseScore and audit note data afterward rather than approximating them through ties.

## Layout and Playback Order

- `order` *(optional array of strings)*: Fully expanded explicit playback sequence of measure IDs, including distinct ending IDs; defaults to source insertion order. Repeated events are never silently transposed or deduced. External callers must preserve reverse mappings by tracking `order[i]`.
- `systems` *(required only for layout)*: System formatting directives:
  - `start`, `end` *(integers)*: 1-based inclusive indices into expanded `order`, covering all measures exactly once.
  - `spacer_slots` *(integer `0..3`)*: Each slot adds a 22-spatia horizontal box containing no music.
  - `page_break` *(boolean)*: Page break applied after the system group.

## CLI Pipeline and Verification

The command-line workflow uses Python 3 standard library scripts alongside headless MuseScore:

1. **Build MusicXML**:
   `python3 /absolute/path/scripts/score.py build ledger.json draft.musicxml`
   Outputs standard MusicXML 4.0.
2. **Compile MSCZ**:
   `"$MUSESCORE_BIN" draft.musicxml -platform offscreen -o draft.mscz`
   Invokes MuseScore via an explicit binary path environment variable with input files listed first.
3. **Verify Draft**:
   `python3 /absolute/path/scripts/score.py verify ledger.json draft.mscz`
4. **Apply System Layout**:
   `python3 /absolute/path/scripts/score.py layout ledger.json draft.mscz laid-out.mscz`
   Applies MuseScore 4 native XML layout rules; version-specific behavior must be verified.
5. **Re-verify and Export**:
   Run `verify` on `laid-out.mscz`. Export PDF via MuseScore, render with `pdftoppm`, and inspect all pages visually.

The `verify` command inspects the single native MSCX score staff inside the MSCZ container. It checks measure count, event sequences, rational durations (including native Tuplet stacks), sounding pitch, string, fret, chord, and rest data. It strictly rejects normalizations that split source events; investigate ledger discrepancies rather than bypassing verification. It does not verify raw photo accuracy, audible waveforms, tie engraving aesthetics, dead notehead styling, or layout appearance, all of which require manual visual inspection.

## Synthetic Example

This synthetic ledger demonstrates meter changes, triplets, explicit unfolding, and system spacing:

```json
{
  "title": "Bass TAB",
  "tuning": [28, 33, 38, 43],
  "measures": [
    {
      "id": "a",
      "meter": [4, 4],
      "key": 0,
      "events": [
        {
          "duration": 4,
          "notes": [{ "string": 4, "fret": 9 }]
        }
      ]
    },
    {
      "id": "b",
      "meter": [2, 4],
      "key": 0,
      "events": [
        { "duration": "1/3", "notes": [{ "string": 3, "fret": 4 }] },
        { "duration": "1/3", "notes": [{ "string": 3, "fret": 6 }] },
        { "duration": "1/3", "notes": [{ "string": 3, "fret": 7 }] },
        { "duration": "1/3", "notes": [{ "string": 2, "fret": 4 }] },
        { "duration": "1/3", "notes": [{ "string": 2, "fret": 6 }] },
        { "duration": "1/3", "notes": [{ "string": 2, "fret": 7 }] }
      ]
    },
    {
      "id": "c",
      "meter": [4, 4],
      "key": 0,
      "events": [
        {
          "duration": 4,
          "notes": []
        }
      ]
    }
  ],
  "order": ["a", "b", "a", "c"],
  "systems": [
    { "start": 1, "end": 3, "spacer_slots": 1, "page_break": false },
    { "start": 4, "end": 4, "spacer_slots": 3, "page_break": false }
  ]
}
```

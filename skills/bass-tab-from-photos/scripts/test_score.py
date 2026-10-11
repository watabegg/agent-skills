"""Offline regression tests with original synthetic music, no scanned score assets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as X
import zipfile
import score


def fixture():
    return {'title': 'Synthetic bass study', 'measures': [
        {'id': 'a', 'key': 4, 'label': 'A', 'events': [{'duration': 4, 'notes': [{'string': 4, 'fret': 9}]}]},
        {'id': 'b', 'meter': [2, 4], 'events': [{'duration': '1/3', 'notes': [{'string': s, 'fret': f}]} for s, f in [(3, 4), (3, 6), (3, 7), (2, 4), (2, 6), (2, 7)]]},
        {'id': 'c', 'events': [{'duration': 4}]}], 'order': ['a', 'b', 'a', 'c'],
        'systems': [{'start': 1, 'end': 3, 'spacer_slots': 1}, {'start': 4, 'end': 4, 'spacer_slots': 3}]}


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def load(self, d):
        p = self.root / 'data.json'; p.write_text(json.dumps(d)); return score.load(p)
    def test_octave_and_repeats(self):
        d, ms = self.load(fixture()); out = self.root / 'score.musicxml'; score.build(d, ms, out)
        r = X.parse(out); bars = r.findall('part/measure'); self.assertEqual(len(bars), 4)
        self.assertEqual([n.findtext('pitch/octave') for n in bars[0].findall('note')], ['3'])
        self.assertEqual(bars[0].findtext('note/pitch/step'), 'C')
        self.assertEqual(bars[0].findtext('note/pitch/alter'), '1')
        self.assertEqual(bars[0].findtext('attributes/transpose/chromatic'), '-12')
        self.assertEqual(sum(int(n.findtext('duration')) for n in bars[1].findall('note')), 480)
        self.assertEqual(bars[2].findtext('note/notations/technical/string'), '4')
    def test_mixed_triplets_have_beat_groups(self):
        events = [{'duration': d, 'notes': [{'string': 4, 'fret': 0}]} for d in ['1/3', '1/6', '1/6', '1/6', '1/6', '1/3', '1/3', '1/3']]
        d, ms = self.load({'measures': [{'id': 'a', 'meter': [2, 4], 'events': events}], 'order': ['a', 'a']})
        out = self.root / 'mixed.musicxml'; score.build(d, ms, out); r = X.parse(out)
        self.assertEqual(len(r.findall(".//tuplet[@type='start']")), 4)
        self.assertEqual(len(r.findall(".//tuplet[@type='stop']")), 4)
        self.assertEqual(len(r.findall('.//time')), 1)
        self.assertEqual(len(r.findall('.//key')), 1)

    def test_invalid_bar_and_float(self):
        for duration in ['7/2', 0.5]:
            d = fixture(); d['measures'][0]['events'][0]['duration'] = duration
            with self.assertRaises(ValueError): self.load(d)
    def test_changed_pitch_tie(self):
        d = fixture(); d['measures'][0]['events'][0]['tie'] = True
        with self.assertRaisesRegex(ValueError, 'identical'): self.load(d)
    def test_custom_tuning_and_string_validation(self):
        d = fixture(); d['tuning'] = [26, 33, 38, 43]; loaded, _ = self.load(d)
        self.assertEqual(score.pitch({'string': 4, 'fret': 9}, loaded['tuning']), 35)
        d['measures'][0]['events'][0]['notes'][0]['string'] = 5
        with self.assertRaises(ValueError): self.load(d)
    def test_native_mismatch_and_no_added_measures(self):
        d = {'measures': [{'id': 'a', 'events': [{'duration': 4, 'notes': [{'string': 4, 'fret': 9}]}]}], 'systems': [{'start': 1, 'end': 1, 'spacer_slots': 3}]}
        d, ms = self.load(d)
        def write(pitch):
            p = self.root / 'test.mscz'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('test.mscx', f'<museScore><Score><Part><Instrument><longName>Bass</longName></Instrument></Part><Staff><Measure><voice><Chord><durationType>whole</durationType><Note><pitch>{pitch}</pitch><string>3</string><fret>9</fret></Note></Chord></voice></Measure></Staff></Score></museScore>')
            return p
        with self.assertRaisesRegex(ValueError, 'mismatch'): score.verify(d, ms, write(42))
        p = write(37); out = self.root / 'layout.mscz'; score.layout(d, ms, p, out)
        z, _, r, st = score.native(out)
        try:
            self.assertEqual(len(st.findall('Measure')), 1)
            self.assertEqual(st.findtext('HBox/width'), '66')
            self.assertIsNone(r.find('Score/Part/Instrument/longName'))
        finally: z.close()


if __name__ == '__main__':
    unittest.main()

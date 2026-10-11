#!/usr/bin/env python3
"""Build, verify and lay out a monophonic/chordal bass TAB from a source ledger."""
import argparse
from fractions import Fraction as F
import json
from pathlib import Path
import xml.etree.ElementTree as X
import zipfile

VALUES = {'whole': F(4), 'half': F(2), 'quarter': F(1), 'eighth': F(1, 2), '16th': F(1, 4), '32nd': F(1, 8)}
NOTATION = {v: (k, False, False) for k, v in VALUES.items()}
NOTATION.update({v * F(3, 2): (k, True, False) for k, v in VALUES.items()})
NOTATION.update({v * F(2, 3): (k, False, True) for k, v in VALUES.items() if k in ('quarter', 'eighth', '16th')})


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    d = json.loads(Path(path).read_text())
    tuning = d.setdefault('tuning', [28, 33, 38, 43])
    require(len(tuning) == 4 and all(type(n) is int and 0 <= n <= 115 for n in tuning), 'Expected four low-to-high sounding MIDI open strings')
    measures = {}
    for m in d['measures']:
        require(isinstance(m['id'], str) and m['id'] not in measures, 'Source IDs must be unique strings')
        measures[m['id']] = m
        meter = m.setdefault('meter', [4, 4])
        require(len(meter) == 2 and all(type(x) is int and x > 0 for x in meter), 'Invalid meter')
        require(meter[1] in (1, 2, 4, 8, 16), 'Unsupported meter denominator')
        require(type(m.get('key', 0)) is int and -7 <= m.get('key', 0) <= 7, 'Invalid key signature')
        if 'tempo' in m:
            require(isinstance(m['tempo'], (int, float)) and 0 < m['tempo'] < 1000, 'Invalid quarter-note BPM')
        total = F(0)
        for e in m['events']:
            require(isinstance(e['duration'], (str, int)), 'Use rational strings, not float durations')
            dur = F(e['duration'])
            require(dur in NOTATION, f"Unsupported duration {dur}")
            total += dur
            ns = e.get('notes', [])
            require(len({n['string'] for n in ns}) == len(ns), 'Two frets on one string in a chord')
            for n in ns:
                require(type(n['string']) is int and 1 <= n['string'] <= 4, 'String 1 is highest; string 4 lowest')
                require(type(n['fret']) is int and 0 <= n['fret'] <= 36, 'Invalid fret')
                require(pitch(n, tuning) <= 115, 'Pitch exceeds written MIDI range after octave transposition')
                require(not n.get('dead') or n['fret'] == 0, 'Dead notes use fret 0 as placeholder')
            require(not e.get('tie') or (ns and not any(n.get('dead') for n in ns)), 'Cannot tie a rest/dead note')
        require(total == F(meter[0] * 4, meter[1]), f"Measure {m['id']}: {total} beats != {meter}")
    order = d.setdefault('order', list(measures))
    require(order and all(n in measures for n in order), 'Order must reference existing source measure IDs')
    seq = [e for i in order for e in measures[i]['events']]
    for j, e in enumerate(seq):
        if e.get('tie'):
            require(j + 1 < len(seq), 'Tie beyond end of performance')
            want = {pitch(n, tuning) for n in e['notes']}
            nxt = {pitch(n, tuning) for n in seq[j + 1].get('notes', []) if not n.get('dead')}
            require(want <= nxt, 'Tie must continue to identical pitch(es); use slurs for pitch changes')
    return d, measures


def pitch(n, tuning):
    return tuning[4 - n['string']] + n['fret']


def sub(p, tag, text=None, **attrs):
    e = X.SubElement(p, tag, {k.replace('_', '-'): str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def written(p, midi):
    step, alter = [('C', 0), ('C', 1), ('D', 0), ('D', 1), ('E', 0), ('F', 0), ('F', 1), ('G', 0), ('G', 1), ('A', 0), ('A', 1), ('B', 0)][midi % 12]
    sub(p, 'step', step)
    if alter:
        sub(p, 'alter', alter)
    sub(p, 'octave', midi // 12 - 1)


def triplet_marks(events):
    """Explicit beat groups for complete quarter-aligned eighth/16th triplets.

    Leave other tuplet structures to import/manual engraving; never infer
    cross-beat groups just from the presence of a 3:2 duration ratio.
    """
    marks = {}; beat = F(0); candidate = []
    for i, e in enumerate(events):
        dur = F(e['duration']); typ, _, triplet = NOTATION[dur]
        if triplet and typ in ('eighth', '16th'):
            if beat.denominator == 1:
                candidate = [i]
            elif candidate:
                candidate.append(i)
            if candidate and (beat + dur).denominator == 1:
                marks[candidate[0]] = 'start'; marks[i] = 'stop'; candidate = []
        else:
            candidate = []
        beat += dur
    return marks


def build(d, measures, output):
    r = X.Element('score-partwise', version='4.0')
    sub(sub(r, 'work'), 'work-title', d.get('title', 'Bass TAB'))
    pl = sub(r, 'part-list'); part = sub(pl, 'score-part', id='P1')
    sub(part, 'part-name', 'Bass')
    sub(sub(part, 'score-instrument', id='I1'), 'instrument-name', 'Electric Bass')
    mi = sub(part, 'midi-instrument', id='I1'); sub(mi, 'midi-channel', 1); sub(mi, 'midi-program', 35)
    part = sub(r, 'part', id='P1'); pending = set(); previous_meter = None; previous_key = None
    for index, source in enumerate(d['order'], 1):
        data = measures[source]; m = sub(part, 'measure', number=index)
        a = sub(m, 'attributes'); sub(a, 'divisions', 240)
        if data.get('key', 0) != previous_key:
            sub(sub(a, 'key'), 'fifths', data.get('key', 0)); previous_key = data.get('key', 0)
        if data['meter'] != previous_meter:
            t = sub(a, 'time'); sub(t, 'beats', data['meter'][0]); sub(t, 'beat-type', data['meter'][1]); previous_meter = data['meter']
        if index == 1:
            clef = sub(a, 'clef'); sub(clef, 'sign', 'TAB'); sub(clef, 'line', 5)
            sd = sub(a, 'staff-details'); sub(sd, 'staff-lines', 4)
            for i, midi in enumerate(d['tuning'], 1):
                st = sub(sd, 'staff-tuning', line=i); tmp = X.Element('pitch'); written(tmp, midi + 12)
                for e in tmp:
                    sub(st, 'tuning-' + e.tag, e.text)
            tr = sub(a, 'transpose'); sub(tr, 'diatonic', -7); sub(tr, 'chromatic', -12)
        for tag in ('label', 'tempo'):
            if tag in data:
                dr = sub(m, 'direction', placement='above'); dt = sub(dr, 'direction-type')
                if tag == 'label':
                    sub(dt, 'rehearsal', data[tag])
                else:
                    met = sub(dt, 'metronome'); sub(met, 'beat-unit', 'quarter'); sub(met, 'per-minute', data[tag]); sub(dr, 'sound', tempo=data[tag])
        markers = triplet_marks(data['events'])
        for event_index, event in enumerate(data['events']):
            dur = F(event['duration']); typ, dot, triplet = NOTATION[dur]
            next_pending = set()
            for i, n in enumerate(event.get('notes') or [None]):
                note = sub(m, 'note')
                if i:
                    sub(note, 'chord')
                midi = pitch(n, d['tuning']) if n else None
                if n:
                    written(sub(note, 'pitch'), midi + 12)
                else:
                    sub(note, 'rest')
                sub(note, 'duration', int(dur * 240))
                ties = (['stop'] if midi in pending else []) + (['start'] if n and event.get('tie') else [])
                for kind in ties:
                    sub(note, 'tie', type=kind)
                sub(note, 'type', typ)
                if dot:
                    sub(note, 'dot')
                if triplet:
                    tm = sub(note, 'time-modification'); sub(tm, 'actual-notes', 3); sub(tm, 'normal-notes', 2)
                if n and n.get('dead'):
                    sub(note, 'notehead', 'x')
                nn = sub(note, 'notations') if n or event_index in markers else None
                if i == 0 and event_index in markers:
                    sub(nn, 'tuplet', type=markers[event_index], number=1, bracket='yes')
                if n:
                    tech = sub(nn, 'technical')
                    sub(tech, 'string', n['string']); sub(tech, 'fret', n['fret'])
                    for kind in ties:
                        sub(nn, 'tied', type=kind)
                    if event.get('tie'):
                        next_pending.add(midi)
            pending = next_pending
    X.indent(r)
    X.ElementTree(r).write(output, encoding='utf-8', xml_declaration=True)


def native(path):
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.endswith('.mscx')]
    require(len(names) == 1, 'Expected one native score XML in MSCZ')
    r = X.fromstring(z.read(names[0]))
    staves = r.findall('Score/Staff')
    require(len(staves) == 1, 'Expected one bass staff')
    return z, names[0], r, staves[0]


def verify(d, measures, path):
    z, _, r, staff = native(path)
    try:
        ms = staff.findall('Measure'); require(len(ms) == len(d['order']), 'Performance measure count differs')
        count = 0
        for idx, (source, m) in enumerate(zip(d['order'], ms), 1):
            actual = []; factor = F(1); stack = []
            voices = m.findall('voice'); require(len(voices) == 1, 'Expected a single rhythm voice')
            for e in voices[0]:
                if e.tag == 'Tuplet':
                    stack.append(factor); factor *= F(int(e.findtext('normalNotes')), int(e.findtext('actualNotes')))
                elif e.tag == 'endTuplet':
                    require(bool(stack), 'Unbalanced tuplet'); factor = stack.pop()
                elif e.tag in ('Chord', 'Rest'):
                    typ = e.findtext('durationType')
                    dur = F(measures[source]['meter'][0] * 4, measures[source]['meter'][1]) if typ == 'measure' else VALUES[typ] * factor
                    dots = int(e.findtext('dots', '0')); dur *= sum(F(1, 2 ** k) for k in range(dots + 1))
                    ns = sorted((int(n.findtext('pitch')), int(n.findtext('string')) + 1, int(n.findtext('fret'))) for n in e.findall('Note'))
                    actual.append((dur, ns)); count += len(ns)
            expected = [(F(e['duration']), sorted((pitch(n, d['tuning']), n['string'], n['fret']) for n in e.get('notes', []))) for e in measures[source]['events']]
            require(actual == expected, f'Performance {idx}, source {source}: event duration/pitch/string/fret mismatch\nexpected={expected}\nactual={actual}')
            require(not stack, 'Unclosed tuplet')
        return {'measures': len(ms), 'notes': count, 'verified': 'native event duration, sounding pitch, string, fret; not photo correctness or tie engraving'}
    finally:
        z.close()


def layout(d, measures, path, output):
    verify(d, measures, path)
    require(Path(path).resolve() != Path(output).resolve(), 'Use a separate layout output')
    z, name, r, staff = native(path)
    try:
        groups = d.get('systems', [])
        require(bool(groups), 'Provide explicit systems based on musical phrases')
        covered = []
        for g in groups:
            require(type(g['start']) is int and type(g['end']) is int and g['end'] >= g['start'], 'Invalid system range')
            covered.extend(range(g['start'], g['end'] + 1))
            require(type(g.get('spacer_slots', 0)) is int and 0 <= g.get('spacer_slots', 0) <= 3, 'Invalid spacer slots')
        require(covered == list(range(1, len(d['order']) + 1)), 'Systems must partition performance measures exactly in order')
        ms = staff.findall('Measure')
        for inst in r.findall('Score/Part/Instrument'):
            for tag in ('longName', 'shortName'):
                for e in inst.findall(tag): inst.remove(e)
        for m in ms:
            for e in m.findall('LayoutBreak'): m.remove(e)
        for g in groups:
            m = ms[g['end'] - 1]; target = m
            if g.get('spacer_slots', 0):
                sub(sub(m, 'LayoutBreak'), 'subtype', 'nobreak')
                target = X.Element('HBox'); sub(target, 'width', 22 * g['spacer_slots']); sub(target, 'boxAutoSize', 0)
                staff.insert(list(staff).index(m) + 1, target)
            sub(sub(target, 'LayoutBreak'), 'subtype', 'page' if g.get('page_break') else 'line')
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as out:
            for n in z.namelist():
                out.writestr(n, X.tostring(r, encoding='utf-8', xml_declaration=True) if n == name else z.read(n))
    finally:
        z.close()
    return verify(d, measures, output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['build', 'verify', 'layout']); p.add_argument('ledger'); p.add_argument('file'); p.add_argument('output', nargs='?')
    args = p.parse_args(); d, ms = load(args.ledger)
    if args.command == 'build':
        require(args.output is None, 'build takes LEDGER OUTPUT.musicxml'); build(d, ms, args.file)
        print(json.dumps({'built': str(args.file), 'measures': len(d['order'])}))
    elif args.command == 'verify':
        require(args.output is None, 'verify takes LEDGER INPUT.mscz'); print(json.dumps(verify(d, ms, args.file)))
    else:
        require(args.output is not None, 'layout takes LEDGER INPUT.mscz OUTPUT.mscz'); print(json.dumps(layout(d, ms, args.file, args.output)))


if __name__ == '__main__':
    main()

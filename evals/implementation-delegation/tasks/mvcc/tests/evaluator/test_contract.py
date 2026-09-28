"""Sealed H1 public-contract assessment: 20 independently scored cases."""
import random
import time
import unittest

from mvcc import Store, Transaction, ConflictError


class HiddenTests(unittest.TestCase):
    def test_01(self):
        """Empty state, public types, atomic versions, and historical deletion."""
        s = Store()
        self.assertEqual(s.version, 0)
        self.assertIsNone(s.read(''))
        self.assertEqual(s.scan(), [])
        t = s.begin()
        self.assertIsInstance(t, Transaction)
        self.assertEqual(t.snapshot, 0)
        self.assertIsNone(t.put('', False))
        self.assertIsNone(t.put('a', 0))
        self.assertEqual(t.commit(), 1)
        t = s.begin()
        self.assertIsNone(t.delete(''))
        self.assertEqual(t.commit(), 2)
        self.assertEqual(s.scan(version=0), [])
        self.assertEqual(s.scan(version=1), [('', False), ('a', 0)])
        self.assertEqual(s.scan(), [('a', 0)])
        self.assertEqual(s.read('', 1), False)
        self.assertIsNone(s.read('', 2))

    def test_02(self):
        """Read and scan consistently reject invalid explicit versions."""
        s = Store()
        t = s.begin()
        t.put('k', 1)
        t.commit()
        for v in [True, False, -1, 2, 1.0, '1', [], {}]:
            with self.subTest(version=v):
                with self.assertRaises(ValueError):
                    s.read('k', v)
                with self.assertRaises(ValueError):
                    s.scan(version=v)
                with self.assertRaises(ValueError):
                    s.scan('z', 'a', version=v)
        self.assertEqual(s.read('k', None), 1)
        self.assertIsNone(s.read('k', 0))
        self.assertEqual(s.version, 1)

    def test_03(self):
        """Lexicographic half-open scans include empty and Unicode keys."""
        s = Store()
        keys = ['é', 'aa', '', 'a', '中', 'b', 'A']
        t = s.begin()
        for k in keys:
            t.put(k, [k])
        t.commit()
        for lo, hi in [(None, None), (None, 'a'), ('', 'b'), ('a', 'aa'),
                       ('aa', None), ('a', 'a'), ('z', 'a'), ('é', '中')]:
            expected = [(k, [k]) for k in sorted(keys)
                        if (lo is None or lo <= k) and (hi is None or k < hi)]
            self.assertEqual(s.scan(lo, hi), expected)
            t = s.begin()
            self.assertEqual(t.scan(lo, hi), expected)
            t.rollback()

    def test_04(self):
        """Old snapshots merge staged inserts, replacements, and tombstones."""
        s = Store()
        t = s.begin()
        t.put('a', 1)
        t.put('c', 3)
        t.commit()
        old = s.begin()
        newer = s.begin()
        newer.put('a', 10)
        newer.put('b', 20)
        newer.commit()
        self.assertEqual(old.snapshot, 1)
        self.assertEqual(old.get('a'), 1)
        self.assertIsNone(old.get('b'))
        old.delete('a')
        old.put('b', 2)
        old.put('c', 30)
        old.put('d', 4)
        old.delete('d')
        self.assertEqual(old.scan(), [('b', 2), ('c', 30)])
        self.assertEqual(old.scan('b', 'c'), [('b', 2)])
        self.assertIsNone(old.get('a'))
        old.rollback()
        self.assertEqual(s.scan(), [('a', 10), ('b', 20), ('c', 3)])

    def test_05(self):
        """Caller values and every read surface are deeply isolated."""
        s = Store()
        data = {'x': [{'y': [1]}]}
        t = s.begin()
        t.put('k', data)
        data['x'][0]['y'].append(2)
        t.get('k')['x'][0]['y'].append(3)
        t.scan()[0][1]['x'][0]['y'].append(4)
        expected = {'x': [{'y': [1]}]}
        self.assertEqual(t.get('k'), expected)
        t.commit()
        s.read('k')['x'][0]['y'].append(5)
        s.scan()[0][1]['x'][0]['y'].append(6)
        old = s.begin()
        old.get('k')['x'][0]['y'].append(7)
        old.scan()[0][1]['x'][0]['y'].append(8)
        newer = s.begin()
        newer.put('k', {'x': []})
        newer.commit()
        s.scan(version=1)[0][1]['x'][0]['y'].append(9)
        self.assertEqual(s.read('k', 1), expected)
        self.assertEqual(old.get('k'), expected)
        old.rollback()

    def test_06(self):
        """Repeated savepoint restoration cannot alias values or later writes."""
        s = Store()
        t = s.begin()
        data = {'v': [1]}
        t.put('a', data)
        t.savepoint('s')
        data['v'].append(2)
        t.put('a', {'v': [3]})
        t.put('b', [])
        t.rollback_to('s')
        value = t.get('a')
        value['v'].append(4)
        t.delete('a')
        t.rollback_to('s')
        self.assertEqual(t.scan(), [('a', {'v': [1]})])
        self.assertEqual(t.commit(), 1)
        self.assertEqual(s.scan(), [('a', {'v': [1]})])

    def test_07(self):
        """Disjoint writers commit while empty final write sets preserve version."""
        s = Store()
        a, b, empty = s.begin(), s.begin(), s.begin()
        a.put('a', 1)
        a.put('b', 2)
        b.put('c', 3)
        self.assertEqual(a.commit(), 1)
        self.assertEqual(b.commit(), 2)
        self.assertEqual(empty.commit(), 2)
        t = s.begin()
        t.savepoint('empty')
        t.put('d', 4)
        t.rollback_to('empty')
        self.assertEqual(t.commit(), 2)
        self.assertEqual(s.scan(version=1), [('a', 1), ('b', 2)])
        self.assertEqual(s.scan(), [('a', 1), ('b', 2), ('c', 3)])

    def test_08(self):
        """Equal puts and absent deletes are writes and cause write conflicts."""
        for initial, operation in [(False, 'delete'), (True, 'put'), (True, 'delete')]:
            with self.subTest(initial=initial, operation=operation):
                s = Store()
                if initial:
                    seed = s.begin()
                    seed.put('k', 1)
                    seed.commit()
                a, b = s.begin(), s.begin()
                a.put('k', 9)
                if operation == 'put':
                    b.put('k', 1)
                else:
                    b.delete('k')
                before = s.version
                self.assertEqual(b.commit(), before + 1)
                with self.assertRaises(ConflictError):
                    a.commit()
                self.assertEqual(s.version, before + 1)

    def test_09(self):
        """Read-only point validation detects ABA, equal writes, and absent deletes."""
        for values in [[1, 2, 1], [1, 1], [None, None], [None, 7, None]]:
            s = Store()
            if values[0] is not None:
                t = s.begin()
                t.put('k', values[0])
                t.commit()
            reader = s.begin()
            self.assertEqual(reader.get('k'), values[0])
            for v in values[1:]:
                t = s.begin()
                if v is None:
                    t.delete('k')
                else:
                    t.put('k', v)
                t.commit()
            with self.assertRaises(ConflictError):
                reader.commit()

    def test_10(self):
        """Reading own writes or deletes remains an obligation after restoration."""
        for delete in [False, True]:
            s = Store()
            t = s.begin()
            t.savepoint('empty')
            if delete:
                t.delete('k')
            else:
                t.put('k', 3)
            self.assertEqual(t.get('k'), None if delete else 3)
            t.rollback_to('empty')
            other = s.begin()
            other.put('k', 8)
            other.commit()
            with self.assertRaises(ConflictError):
                t.commit()
            self.assertEqual(s.version, 1)

    def test_11(self):
        """Empty range scans detect hidden phantoms and include only their bounds."""
        for changed, conflict in [('a', True), ('m', True), ('z', False), ('', False)]:
            s = Store()
            reader = s.begin()
            self.assertEqual(reader.scan('a', 'z'), [])
            for deleting in [False, True]:
                w = s.begin()
                if deleting:
                    w.delete(changed)
                else:
                    w.put(changed, 1)
                w.commit()
            self.assertEqual(s.scan(), [])
            if conflict:
                with self.assertRaises(ConflictError):
                    reader.commit()
            else:
                self.assertEqual(reader.commit(), 2)

    def test_12(self):
        """Empty/inverted ranges have no obligations; unbounded ranges do."""
        for lo, hi, conflict in [('m', 'm', False), ('z', 'a', False),
                                  (None, 'a', False), ('z', None, False),
                                  (None, None, True), (None, 'z', True),
                                  ('a', None, True)]:
            s = Store()
            t = s.begin()
            self.assertEqual(t.scan(lo, hi), [])
            w = s.begin()
            w.delete('m')
            w.commit()
            if conflict:
                with self.assertRaises(ConflictError):
                    t.commit()
            else:
                self.assertEqual(t.commit(), 1)

    def test_13(self):
        """Rollback restores writes, keeps its marker, and drops later markers."""
        s = Store()
        t = s.begin()
        t.put('k', 0)
        self.assertIsNone(t.savepoint('a'))
        t.put('k', 1)
        t.savepoint('b')
        t.delete('k')
        t.savepoint('c')
        t.put('x', 2)
        self.assertIsNone(t.rollback_to('b'))
        self.assertEqual(t.scan(), [('k', 1)])
        with self.assertRaises(ValueError):
            t.rollback_to('c')
        with self.assertRaises(ValueError):
            t.savepoint('b')
        t.savepoint('c')
        t.rollback_to('a')
        self.assertEqual(t.get('k'), 0)
        with self.assertRaises(ValueError):
            t.release('b')
        t.savepoint('b')
        t.rollback_to('a')
        self.assertEqual(t.commit(), 1)
        self.assertEqual(s.scan(), [('k', 0)])

    def test_14(self):
        """Release preserves writes; invalid savepoint operations change nothing."""
        s = Store()
        t = s.begin()
        t.savepoint('')
        t.put('k', 1)
        t.savepoint('b')
        t.put('k', 2)
        t.savepoint('c')
        for method, name in [(t.savepoint, 'b'), (t.rollback_to, 'missing'),
                             (t.release, 'missing')]:
            with self.assertRaises(ValueError):
                method(name)
            self.assertEqual(t.get('k'), 2)
        self.assertIsNone(t.release('b'))
        self.assertEqual(t.get('k'), 2)
        for name in ['b', 'c']:
            with self.assertRaises(ValueError):
                t.rollback_to(name)
            t.savepoint(name)
        t.rollback_to('')
        self.assertEqual(t.scan(), [])
        t.put('z', 3)
        t.release('')
        self.assertEqual(t.commit(), 1)
        self.assertEqual(s.scan(), [('z', 3)])

    def test_15(self):
        """Point/range obligations survive rollback-to and release interactions."""
        for read_kind in ['point', 'range']:
            for action in ['rollback', 'release']:
                s = Store()
                t = s.begin()
                t.savepoint('a')
                t.put('local', 1)
                t.savepoint('b')
                if read_kind == 'point':
                    t.get('m')
                else:
                    t.scan('m', 'n')
                if action == 'rollback':
                    t.rollback_to('a')
                else:
                    t.release('a')
                w = s.begin()
                w.delete('m')
                w.commit()
                with self.assertRaises(ConflictError):
                    t.commit()
                self.assertIsNone(s.read('local'))

    def test_16(self):
        """Success, rollback, and conflict close every transaction operation."""
        for ending in ['commit', 'rollback', 'conflict']:
            s = Store()
            t = s.begin()
            t.savepoint('s')
            t.put('k', 1)
            if ending == 'commit':
                t.commit()
            elif ending == 'rollback':
                self.assertIsNone(t.rollback())
                self.assertEqual(s.version, 0)
            else:
                w = s.begin()
                w.put('k', 2)
                w.commit()
                with self.assertRaises(ConflictError):
                    t.commit()
            self.assertEqual(t.snapshot, 0)
            for call in [lambda: t.get('k'), lambda: t.put('k', 3),
                         lambda: t.delete('k'), lambda: t.scan(), t.commit,
                         t.rollback, lambda: t.savepoint('new'),
                         lambda: t.rollback_to('s'), lambda: t.release('s')]:
                with self.assertRaises(RuntimeError):
                    call()

    def test_17(self):
        """Conflict is atomic, while discarded write intentions do not conflict."""
        s = Store()
        doomed, restored = s.begin(), s.begin()
        doomed.put('a', 1)
        doomed.put('z', 1)
        doomed.put('b', 1)
        restored.savepoint('empty')
        restored.put('z', 9)
        restored.rollback_to('empty')
        restored.put('safe', 4)
        w = s.begin()
        w.put('z', 2)
        w.commit()
        with self.assertRaises(ConflictError):
            doomed.commit()
        self.assertEqual(s.scan(), [('z', 2)])
        self.assertEqual(s.version, 1)
        self.assertEqual(restored.commit(), 2)
        self.assertEqual(s.scan(), [('safe', 4), ('z', 2)])

    def test_18(self):
        """Deterministic tiny histories agree with independent event-set validation."""
        rng = random.Random(81731)
        keys = ['', 'a', 'b', 'c']
        for case in range(80):
            s = Store()
            base = {k: rng.randrange(5) for k in keys if rng.randrange(2)}
            seed = s.begin()
            for k, v in base.items():
                seed.put(k, v)
            seed.commit()
            t = s.begin()
            points = set(rng.sample(keys, rng.randrange(5)))
            lo, hi = rng.choice([(None, None), ('a', 'c'), ('b', 'b'), ('c', 'a'), ('', 'b')])
            writes = {k: rng.choice([None, 0, 9]) for k in rng.sample(keys, rng.randrange(5))}
            for k, v in writes.items():
                t.delete(k) if v is None else t.put(k, v)
            view = dict(base)
            for k, v in writes.items():
                if v is None:
                    view.pop(k, None)
                else:
                    view[k] = v
            for k in points:
                self.assertEqual(t.get(k), view.get(k))
            self.assertEqual(t.scan(lo, hi), [(k, view[k]) for k in sorted(view)
                             if (lo is None or lo <= k) and (hi is None or k < hi)])
            changed = set()
            current = dict(base)
            for _ in range(rng.randrange(4)):
                k, v = rng.choice(keys), rng.choice([None, 0, 9])
                changed.add(k)
                w = s.begin()
                if v is None:
                    w.delete(k)
                    current.pop(k, None)
                else:
                    w.put(k, v)
                    current[k] = v
                w.commit()
            conflict = any(k in points or k in writes or
                           ((lo is None or lo <= k) and (hi is None or k < hi))
                           for k in changed)
            before = s.version
            if conflict:
                with self.assertRaises(ConflictError, msg=str(case)):
                    t.commit()
                self.assertEqual(s.version, before)
            else:
                self.assertEqual(t.commit(), before + bool(writes))
                for k, v in writes.items():
                    if v is None:
                        current.pop(k, None)
                    else:
                        current[k] = v
            self.assertEqual(s.scan(), sorted(current.items()))

    def test_19(self):
        """A thousand versions retain history with 200 keys and 100 open readers."""
        s = Store()
        readers = []
        for v in range(1, 1001):
            t = s.begin()
            t.put('k%03d' % ((v - 1) % 200), v)
            self.assertEqual(t.commit(), v)
            if v % 10 == 0:
                readers.append(s.begin())
        for t in readers:
            snap = t.snapshot
            expected = []
            for i in range(200):
                first = i + 1
                if first <= snap:
                    expected.append(('k%03d' % i, first + 200 * ((snap - first) // 200)))
            self.assertEqual(t.scan(), expected)
            self.assertEqual(s.scan(version=snap), expected)
            if snap < 1000:
                with self.assertRaises(ConflictError):
                    t.commit()
            else:
                self.assertEqual(t.commit(), 1000)
        self.assertEqual(s.scan(version=0), [])
        self.assertEqual(s.version, 1000)

    def test_20(self):
        """Opening 100 transactions is inexpensive despite large nested values."""
        s = Store()
        seed = s.begin()
        payload = {'items': list(range(1000))}
        for i in range(200):
            seed.put('k%03d' % i, payload)
        seed.commit()
        start = time.perf_counter()
        readers = [s.begin() for _ in range(100)]
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.5, '100 begin() calls should not deep-copy the store')
        w = s.begin()
        w.put('k000', {'items': [-1]})
        w.commit()
        for t in readers:
            self.assertEqual(t.snapshot, 1)
            self.assertEqual(t.get('k000'), payload)
            t.rollback()
        self.assertEqual(s.read('k000'), {'items': [-1]})


if __name__ == '__main__':
    unittest.main()

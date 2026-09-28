"""Sealed public-contract tests; each test is worth four correctness points."""
import itertools
import random
import unittest

from buildgraph import BuildGraph


def tiny_expected(deps, changed, targets=None):
    """Bounded fixture oracle: fixed-point sets, then exhaustive permutations."""
    assert len(deps) <= 8
    dirty = set(changed)
    for _ in deps:
        dirty |= {n for n, ds in deps.items() if set(ds) & dirty}
    required = set(deps) if targets is None else set(targets)
    for _ in deps:
        required |= {d for n in tuple(required) for d in deps[n]}
    selected = dirty & required
    for order in itertools.permutations(n for n in deps if n in selected):
        positions = {n: i for i, n in enumerate(order)}
        if all(positions[d] < positions[n] for n in order
               for d in deps[n] if d in selected):
            return list(order)
    raise AssertionError('A DAG must admit an ordering')


def tiny_path(deps, changed, node):
    """Enumerate paths only in tiny DAG fixtures; no production-size solver."""
    assert len(deps) <= 8
    rank = {n: i for i, n in enumerate(deps)}
    paths = []
    pending = [[n] for n in set(changed)]
    while pending:
        path = pending.pop()
        if path[-1] == node:
            paths.append(path)
        else:
            pending.extend(path + [n] for n in deps if path[-1] in deps[n])
    return min(paths, key=lambda p: (len(p), tuple(rank[n] for n in p))) if paths else None


class BuildGraphContract(unittest.TestCase):
    def test_01(self):
        """Empty graphs and empty query iterables have exact public return values."""
        g = BuildGraph({})
        self.assertEqual(g.plan(iter(())), [])
        self.assertEqual(g.plan(iter(()), iter(())), [])
        with self.assertRaises(ValueError):
            g.explain([], '')
        self.assertIsNone(g.replace({'': []}))
        self.assertEqual(g.plan(['']), [''])
        self.assertEqual(g.explain([''], ''), [''])

    def test_02(self):
        """Graph and dependency containers must have the specified dict/list types."""
        for bad in (None, [], (), 'x', [('a', [])], {'a': ()},
                    {'a': set()}, {'a': 'a'}, {'a': None}, {'a': {}}):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                BuildGraph(bad)

    def test_03(self):
        """Non-string graph IDs and dangling dependencies raise ValueError."""
        for bad in ({1: []}, {None: []}, {'a': [1]}, {'a': [None]},
                    {'a': [[]]}, {'a': ['missing']}, {'a': ['']}):
            with self.subTest(bad=repr(bad)), self.assertRaises(ValueError):
                BuildGraph(bad)

    def test_04(self):
        """Self-loops and cycles in disconnected components are rejected."""
        for bad in ({'a': ['a']}, {'a': ['b'], 'b': ['a']},
                    {'ok': [], 'a': ['c'], 'b': ['a'], 'c': ['b']},
                    {'ok': [], 'x': ['x', 'x']}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                BuildGraph(bad)

    def test_05(self):
        """Dependency and query duplicates count once, with insertion-rank ordering."""
        g = BuildGraph({'z': [], 'b': ['z', 'z'], 'a': ['z'], 'end': ['b', 'a', 'b']})
        self.assertEqual(g.plan(['z', 'z'], ['end', 'end']), ['z', 'b', 'a', 'end'])
        self.assertEqual(g.explain(['z', 'z'], 'end'), ['z', 'b', 'end'])

    def test_06(self):
        """Every ready-node choice rechecks permanent rank, rather than using FIFO."""
        g = BuildGraph({'low': ['root'], 'root': [], 'tail': []})
        self.assertEqual(g.plan(['tail', 'root']), ['root', 'low', 'tail'])
        self.assertEqual(g.plan(['low', 'tail']), ['low', 'tail'])

    def test_07(self):
        """Dirty closure follows transitive dependents and leaves prerequisites clean."""
        g = BuildGraph({'a': [], 'b': ['a'], 'c': ['b'], 'd': ['a'], 'e': ['c', 'd'], 'u': []})
        self.assertEqual(g.plan(['b']), ['b', 'c', 'e'])
        self.assertEqual(g.plan(['a']), ['a', 'b', 'c', 'd', 'e'])
        self.assertEqual(g.plan([]), [])
        self.assertIsNone(g.explain(['b'], 'a'))

    def test_08(self):
        """Target ancestors intersect full dirty closure; clean inputs never block output."""
        g = BuildGraph({'out': ['mid', 'clean'], 'mid': ['src'], 'src': [],
                        'clean': [], 'other': ['src'], 'unrelated': []})
        self.assertEqual(g.plan(['src'], ['out']), ['src', 'mid', 'out'])
        self.assertEqual(g.plan(['mid'], ['out']), ['mid', 'out'])
        self.assertEqual(g.plan(['src'], ['mid']), ['src', 'mid'])
        self.assertEqual(g.plan(['unrelated'], ['out']), [])
        self.assertEqual(g.plan(['src'], []), [])

    def test_09(self):
        """One-shot iterables are consumed correctly and irrelevant unknown IDs rejected."""
        g = BuildGraph({'a': [], 'b': ['a'], 'c': []})
        self.assertEqual(g.plan((x for x in ['a', 'a']), (x for x in ['b', 'b'])), ['a', 'b'])
        self.assertEqual(g.explain((x for x in ['a', 'a']), 'b'), ['a', 'b'])
        for changed, targets in ((['bad'], []), ([], ['bad']), (['a'], ['b', 'bad'])):
            with self.assertRaises(ValueError):
                g.plan(iter(changed), iter(targets))
        for changed, node in ((['b', 'bad'], 'b'), ([], 'bad'), (['bad'], 'c')):
            with self.assertRaises(ValueError):
                g.explain(iter(changed), node)

    def test_10(self):
        """Explanation minimizes distance before rank and changed nodes explain themselves."""
        g = BuildGraph({'early': [], 'hop': ['early'], 'late': [], 'end': ['hop', 'late'], 'u': []})
        self.assertEqual(g.explain(['early', 'late'], 'end'), ['late', 'end'])
        self.assertEqual(g.explain(['early', 'end'], 'end'), ['end'])
        self.assertIsNone(g.explain(['early'], 'u'))
        self.assertIsNone(g.explain([], 'end'))

    def test_11(self):
        """Equal-length explanations compare full rank sequences, not just parents."""
        g = BuildGraph({'s1': [], 's2': [], 'p2': ['s2'], 'p1': ['s1'],
                        'end': ['p2', 'p1'], 'branch': ['s1']})
        self.assertEqual(g.explain(['s2', 's1'], 'end'), ['s1', 'p1', 'end'])
        h = BuildGraph({'s': [], 'first': ['s'], 'second': ['s'],
                        'end': ['second', 'first']})
        self.assertEqual(h.explain(['s'], 'end'), ['s', 'first', 'end'])

    def test_12(self):
        """All four-node forward DAGs and changed subsets agree with bounded exhaustive oracles."""
        nodes = ['n0', 'n1', 'n2', 'n3']
        edges = [(nodes[j], nodes[i]) for j in range(4) for i in range(j)]
        for mask in range(1 << len(edges)):
            deps = {nodes[i]: [] for i in (2, 0, 3, 1)}
            for bit, (n, d) in enumerate(edges):
                if mask & (1 << bit):
                    deps[n].append(d)
            g = BuildGraph(deps)
            for cm in range(16):
                changed = [nodes[i] for i in range(4) if cm & (1 << i)]
                with self.subTest(graph=mask, changed=cm):
                    for targets in (None, [], ['n3'], ['n1', 'n2']):
                        self.assertEqual(g.plan(iter(changed), None if targets is None else iter(targets)),
                                         tiny_expected(deps, changed, targets))
                    for node in nodes:
                        self.assertEqual(g.explain(iter(changed), node), tiny_path(deps, changed, node))

    def test_13(self):
        """Eight-node adversarial DAGs combine reordered ranks, duplicate edges and multi-source paths."""
        rng = random.Random(9137)
        for case in range(12):
            ids = list('abcdefgh')
            rank_order = ids[:]
            rng.shuffle(rank_order)
            deps = {n: [] for n in rank_order}
            for j in range(1, 8):
                deps[ids[j]] = [ids[j - 1]] + [ids[i] for i in range(j) if rng.random() < .45]
                rng.shuffle(deps[ids[j]])
            g = BuildGraph(deps)
            changed = rng.sample(ids, 3)
            with self.subTest(case=case):
                for targets in (None, ['h'], ['d', 'f'], []):
                    self.assertEqual(g.plan(iter(changed), None if targets is None else iter(targets)),
                                     tiny_expected(deps, changed, targets))
                for node in ids:
                    self.assertEqual(g.explain(iter(reversed(changed)), node), tiny_path(deps, changed, node))

    def test_14(self):
        """Constructor and replacement snapshot caller-owned dictionaries and nested lists."""
        deps = {'a': [], 'b': ['a']}
        g = BuildGraph(deps)
        deps['b'].clear()
        deps['a'].append('missing')
        deps.clear()
        self.assertEqual(g.plan(['a']), ['a', 'b'])
        self.assertEqual(g.explain(['a'], 'b'), ['a', 'b'])
        replacement = {'y': ['x'], 'x': []}
        self.assertIsNone(g.replace(replacement))
        replacement['y'].clear()
        replacement['x'].append('y')
        replacement['new'] = []
        self.assertEqual(g.plan(['x']), ['x', 'y'])
        self.assertEqual(g.explain(['x'], 'y'), ['x', 'y'])
        with self.assertRaises(ValueError):
            g.plan(['new'])

    def test_15(self):
        """Queries preserve inputs and graph state; returned lists are independently mutable."""
        g = BuildGraph({'b': ['a'], 'a': [], 'c': []})
        changed, targets = ['a', 'a'], ['b', 'b']
        plan = g.plan(changed, targets)
        path = g.explain(changed, 'b')
        self.assertEqual(changed, ['a', 'a'])
        self.assertEqual(targets, ['b', 'b'])
        plan[:] = ['corrupt']
        path.clear()
        self.assertEqual(g.plan(changed, targets), ['a', 'b'])
        self.assertEqual(g.explain(changed, 'b'), ['a', 'b'])
        self.assertEqual(g.plan(['c']), ['c'])
        self.assertEqual(g.plan([]), [])
        with self.assertRaises(ValueError):
            g.plan(['a', 'bad'])
        self.assertEqual(g.plan(['a']), ['a', 'b'])

    def test_16(self):
        """Successful replacement changes node set, rank and explanation tie-breaking immediately."""
        g = BuildGraph({'a': [], 'b': [], 'end': ['a', 'b']})
        self.assertEqual(g.explain(['a', 'b'], 'end'), ['a', 'end'])
        self.assertIsNone(g.replace({'b': [], 'a': [], 'end': ['a', 'b']}))
        self.assertEqual(g.plan(['a', 'b']), ['b', 'a', 'end'])
        self.assertEqual(g.explain(['a', 'b'], 'end'), ['b', 'end'])
        self.assertIsNone(g.replace({'new': []}))
        self.assertEqual(g.plan(['new']), ['new'])
        with self.assertRaises(ValueError):
            g.explain([], 'a')
        self.assertIsNone(g.replace({}))
        self.assertEqual(g.plan([]), [])

    def test_17(self):
        """Failed replacements atomically retain old topology, node membership and ranks."""
        g = BuildGraph({'z': [], 'a': [], 'end': ['z', 'a']})
        for bad in ({'new': [], 'x': ['missing']}, {'a': ['z'], 'z': ['a']},
                    {'new': [], 'a': ()}, {3: []}, None):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ValueError):
                    g.replace(bad)
                self.assertEqual(g.plan(['a', 'z']), ['z', 'a', 'end'])
                self.assertEqual(g.explain(['a', 'z'], 'end'), ['z', 'end'])
                with self.assertRaises(ValueError):
                    g.plan(['new'])

    def test_18(self):
        """Empty IDs interact correctly with targets, duplicates, clean inputs and validation."""
        g = BuildGraph({'end': ['', 'clean', ''], '': ['seed'], 'clean': [], 'seed': [], 'side': ['seed']})
        self.assertEqual(g.plan(iter(['seed', 'seed']), iter(['end', 'end'])), ['seed', '', 'end'])
        self.assertEqual(g.plan([''], ['end']), ['', 'end'])
        self.assertEqual(g.explain(['seed'], ''), ['seed', ''])
        self.assertEqual(g.explain(['', 'seed'], 'end'), ['', 'end'])
        self.assertEqual(g.plan(['seed'], ['clean']), [])
        with self.assertRaises(ValueError):
            g.explain(iter(['', 'missing']), '')
        self.assertEqual(g.plan([''], []), [])

    def test_19(self):
        """A 10,000-node reverse-ranked chain supports deep build and explanation traversals."""
        size = 10000
        ids = [f'n{i}' for i in range(size)]
        deps = {ids[i]: [ids[i - 1]] if i else [] for i in reversed(range(size))}
        g = BuildGraph(deps)
        self.assertEqual(g.plan([ids[0]]), ids)
        self.assertEqual(g.plan([ids[5000]], [ids[-1]]), ids[5000:])
        self.assertEqual(g.explain([ids[0]], ids[-1]), ids)
        self.assertEqual(g.explain([ids[5000], ids[0]], ids[-1]), ids[5000:])

    def test_20(self):
        """A 10,000-node, 30,000-edge DAG avoids path enumeration and handles wide readiness."""
        roots = [f'r{i}' for i in range(100)]
        sinks = [f's{i}' for i in range(100)]
        middle = [[f'm{layer}_{i}' for i in range(100)] for layer in range(98)]
        deps = {n: [] for n in sinks}
        for row in reversed(middle):
            for n in row:
                deps[n] = []
        deps.update({n: [] for n in roots})
        previous = roots
        for row in middle:
            for i, n in enumerate(row):
                deps[n] = [previous[i], previous[(i + 1) % 100], previous[(i + 2) % 100]]
            previous = row
        for i, n in enumerate(sinks):
            deps[n] = [previous[(i + k) % 100] for k in range(6)]
        self.assertEqual(sum(map(len, deps.values())), 30000)
        g = BuildGraph(deps)
        # All prerequisites in each layer precede its release; a single earlier
        # root can release nodes sooner, so check exact rank choices independently.
        result = g.plan(iter(roots))
        self.assertEqual(len(result), 10000)
        self.assertEqual(set(result), set(deps))
        rank = {n: i for i, n in enumerate(deps)}
        # Maintain ready membership from public fixture edges, not candidate state.
        children = {n: [] for n in deps}
        counts = {n: len(ds) for n, ds in deps.items()}
        import heapq
        ready = [(rank[n], n) for n in deps if not counts[n]]
        heapq.heapify(ready)
        for n, ds in deps.items():
            for d in ds:
                children[d].append(n)
        for actual in result:
            self.assertEqual(actual, heapq.heappop(ready)[1])
            for child in children[actual]:
                counts[child] -= 1
                if counts[child] == 0:
                    heapq.heappush(ready, (rank[child], child))
        expected = [roots[0]] + [row[0] for row in middle] + [sinks[0]]
        self.assertEqual(g.explain(iter(reversed(roots)), sinks[0]), expected)
        self.assertEqual(g.plan(roots, []), [])


if __name__ == '__main__':
    unittest.main()

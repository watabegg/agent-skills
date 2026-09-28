import unittest
from buildgraph import BuildGraph
class Smoke(unittest.TestCase):
 def test_chain(self):
  x=BuildGraph({'b':['a'],'a':[]}); self.assertEqual(x.plan(['a']),['a','b']); self.assertEqual(x.explain(['a'],'b'),['a','b'])
if __name__=='__main__': unittest.main()

import unittest
from mvcc import Store
class Smoke(unittest.TestCase):
 def test_write(self):
  s=Store(); t=s.begin(); t.put('a',{'x':[1]}); self.assertEqual(t.commit(),1); self.assertEqual(s.read('a'),{'x':[1]}); self.assertIsNone(s.read('a',0))
if __name__=='__main__': unittest.main()

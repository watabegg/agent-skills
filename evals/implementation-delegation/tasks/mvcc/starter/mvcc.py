class ConflictError(Exception):
    pass

class Store:
    def __init__(self):
        self.version = 0
    def begin(self):
        raise NotImplementedError
    def read(self, key, version=None):
        raise NotImplementedError
    def scan(self, start=None, end=None, version=None):
        raise NotImplementedError

class Transaction:
    pass

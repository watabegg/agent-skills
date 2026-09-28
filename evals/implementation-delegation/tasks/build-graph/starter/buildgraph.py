class BuildGraph:
    def __init__(self, dependencies):
        self.replace(dependencies)
    def replace(self, dependencies):
        raise NotImplementedError
    def plan(self, changed, targets=None):
        raise NotImplementedError
    def explain(self, changed, node):
        raise NotImplementedError

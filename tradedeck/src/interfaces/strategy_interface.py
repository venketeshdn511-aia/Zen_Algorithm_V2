
class StrategyInterface:
    """Minimal interface to satisfy inheritance in the provided strategy Adapter."""
    def __init__(self, name: str):
        self.name = name

    def calculate_signal(self, df):
        raise NotImplementedError("Subclasses must implement calculate_signal")

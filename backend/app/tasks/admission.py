"""Event-loop-owned fair page admission; only ready pages enter this queue."""
from collections import OrderedDict, deque
from collections.abc import Callable


class PageAdmission:
    def __init__(self):
        self.queues: dict[bool, OrderedDict[str, deque[str]]] = {True:OrderedDict(),False:OrderedDict()}
        self.interactive_streak = 0

    def add(self, token: str, project: str, *, interactive: bool):
        self.queues[interactive].setdefault(project,deque()).append(token)

    def remove(self, token: str):
        for tier in self.queues.values():
            for project, pending in list(tier.items()):
                try:
                    pending.remove(token)
                except ValueError:
                    continue
                if not pending:
                    del tier[project]
                return

    def promote(self, token: str):
        tier = self.queues[False]
        for project,pending in list(tier.items()):
            if token in pending:
                pending.remove(token)
                if not pending:
                    del tier[project]
                self.add(token,project,interactive=True)
                return

    def claim(self, token: str, acquire: Callable[[], bool]) -> bool:
        interactive = bool(self.queues[True]) and (self.interactive_streak < 3 or not self.queues[False])
        tier = self.queues[interactive]
        if not tier:
            return False
        project,pending = next(iter(tier.items()))
        if pending[0] != token or not acquire():
            return False
        pending.popleft()
        if pending:
            tier.move_to_end(project)
        else:
            del tier[project]
        self.interactive_streak = self.interactive_streak + 1 if interactive else 0
        return True

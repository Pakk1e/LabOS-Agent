from labos_agent.browser.detection import observe

class FakeLocator:
    def __init__(self,count=0,visible=False): self._count=count; self._visible=visible
    def count(self): return self._count
    @property
    def first(self): return self
    def is_visible(self): return self._visible

class FakePage:
    def __init__(self,stop=False): self.stop=stop
    def locator(self,selector):
        if "Stop" in selector or "stop" in selector: return FakeLocator(1,self.stop)
        if "assistant" in selector: return FakeLocator(2,True)
        return FakeLocator(1,True)

def test_observe_detects_stop_control():
    obs=observe(FakePage(stop=True))
    assert not obs.generating and obs.stop_control_visible and obs.input_available
    assert obs.assistant_count==2

def test_observe_without_stop_is_not_generating():
    assert not observe(FakePage(stop=False)).generating


def test_observe_does_not_report_generation_without_stop_control():
    obs = observe(FakePage(stop=False))
    assert not obs.generating


class _RealisticLocator(FakeLocator):
    def __init__(self, visible=True, editable=True):
        super().__init__(1, visible)
        self.editable = editable

    def evaluate(self, expression):
        return "div"

    def get_attribute(self, name):
        return {"contenteditable": "true", "role": "textbox"}.get(name)

    def is_hidden(self):
        return not self._visible

    def is_disabled(self):
        return False

    def is_editable(self):
        return self.editable


class _RealisticPage:
    def __init__(self, editable=True, stop=False):
        self.composer = _RealisticLocator(editable=editable)
        self.stop = stop

    def locator(self, selector):
        if "Stop" in selector or "stop" in selector:
            return FakeLocator(1, self.stop)
        if "assistant" in selector:
            return FakeLocator(1, True)
        return self.composer


def test_observe_requires_editable_real_composer():
    assert observe(_RealisticPage(editable=True)).input_available
    assert not observe(_RealisticPage(editable=False)).input_available

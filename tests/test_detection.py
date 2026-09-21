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
    assert obs.generating and obs.stop_control_visible and obs.input_available
    assert obs.assistant_count==2

def test_observe_without_stop_is_not_generating():
    assert not observe(FakePage(stop=False)).generating

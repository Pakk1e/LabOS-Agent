from pathlib import Path
from labos_agent.config import AppConfig, BrowserConfig, ProjectConfig
from labos_agent.loop import run_loop

class FakeSession:
    def __init__(self,*args,**kwargs): self.context=type("Context",(),{"pages":[object()]})()
    def __enter__(self): return self
    def __exit__(self,*args): pass

class FakeChat:
    def __init__(self,page): pass
    def assert_ready(self): pass
    def project_context_present(self,name): return True
    def send_and_wait_for_response(self,*args,**kwargs): return "no execution request"

def test_run_loop_selects_project_page_and_stops_at_iteration_limit(monkeypatch,tmp_path):
    project=ProjectConfig(name="test",repository="x",project_root=tmp_path,continuation_message="continue",project_name="Test",execution_enabled=False)
    config=AppConfig(browser=BrowserConfig(profile_dir=tmp_path),projects={"test":project})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("labos_agent.loop.BrowserSession",FakeSession)
    monkeypatch.setattr("labos_agent.loop.ChatGPTPage",FakeChat)
    monkeypatch.setattr("labos_agent.loop.prepare_repository",lambda *args,**kwargs: None)
    monkeypatch.setattr("labos_agent.loop.inspect_project",lambda *args,**kwargs: "snapshot")
    monkeypatch.setattr("labos_agent.loop.git_snapshot",lambda *args,**kwargs: type("S",(),{"head":"h","upstream":"u","status":""})())
    monkeypatch.setattr("labos_agent.loop.build_continuation_prompt",lambda *args,**kwargs: "prompt")
    result=run_loop(config,"test",deadline=None,max_iterations=1,max_rollovers=1)
    assert result.state.iteration == 1
    assert result.state.reason == "maximum iterations reached"

"""Persistent controller state and explicit state transitions."""
from __future__ import annotations
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from enum import Enum
import json
from pathlib import Path

class RunState(str,Enum):
    IDLE="IDLE"; STARTING="STARTING"; WORKING="WORKING"; ITERATION_SUCCEEDED="ITERATION_SUCCEEDED"
    ROLLOVER="ROLLOVER"; BLOCKED="BLOCKED"; STOPPED="STOPPED"; COMPLETED="COMPLETED"; ERROR="ERROR"

class IterationStage(str,Enum):
    IDLE="IDLE"
    ITERATION_STARTED="ITERATION_STARTED"
    CHATGPT_WORKING="CHATGPT_WORKING"
    EXECUTION_REQUIRED="EXECUTION_REQUIRED"
    EXECUTION_APPLIED="EXECUTION_APPLIED"
    PROGRESS_VERIFIED="PROGRESS_VERIFIED"
    CI_RUNNING="CI_RUNNING"
    CI_PASSED="CI_PASSED"
    COMMITTING="COMMITTING"
    PUSHING="PUSHING"
    REMOTE_VERIFIED="REMOTE_VERIFIED"
    ITERATION_SUCCEEDED="ITERATION_SUCCEEDED"

@dataclass
class AgentState:
    project:str
    run_id:str
    branch_name:str="main"
    state:RunState=RunState.IDLE
    iteration:int=0
    rollover_count:int=0
    consecutive_failures:int=0
    consecutive_no_progress:int=0
    iteration_at_last_rollover:int=0
    started_at:str|None=None
    stopped_at:str|None=None
    last_action:str|None=None
    reason:str|None=None
    failure_history:list[dict[str,str|int]]=field(default_factory=list)
    last_ci_result:str|None=None
    last_progress_result:str|None=None
    pending_ci_fix:bool=False
    pending_ci_baseline_untracked:list[str]=field(default_factory=list)
    pending_ci_worktree_fingerprint:str|None=None
    last_commit_sha:str|None=None

    # Evidence for the currently persisted/most recently completed iteration.
    iteration_stage:IterationStage=IterationStage.IDLE
    iteration_started_sha:str|None=None
    execution_requested:bool=False
    execution_applied:bool=False
    files_changed:bool=False
    meaningful_progress:bool=False
    local_ci_ran:bool=False
    local_ci_passed:bool=False
    commit_created:bool=False
    push_verified:bool=False
    remote_sha:str|None=None
    github_ci_verified:bool=False
    pending_remote_ci_fix:bool=False
    pending_remote_ci_sha:str|None=None
    pending_remote_ci_result:str|None=None

    def transition(self,state:RunState,*,reason:str|None=None)->None:
        self.state=state; self.reason=reason; self.last_action=state.value
        if state in {RunState.STOPPED,RunState.COMPLETED,RunState.ERROR,RunState.BLOCKED}: self.stopped_at=utc_now()
        if self.started_at is None and state!=RunState.IDLE: self.started_at=utc_now()

    def set_iteration_stage(self,stage:IterationStage)->None:
        self.iteration_stage=stage
        self.last_action=stage.value

    def start_iteration(self,starting_sha:str)->None:
        self.iteration_started_sha=starting_sha
        self.execution_requested=False
        self.execution_applied=False
        self.files_changed=False
        self.meaningful_progress=False
        self.local_ci_ran=False
        self.local_ci_passed=False
        self.commit_created=False
        self.push_verified=False
        self.remote_sha=None
        self.github_ci_verified=False
        self.pending_remote_ci_fix=False
        self.pending_remote_ci_sha=None
        self.pending_remote_ci_result=None
        self.set_iteration_stage(IterationStage.ITERATION_STARTED)

    def record_failure(self,reason:str)->None:
        self.failure_history.append({"timestamp":utc_now(),"iteration":self.iteration,"reason":reason})
        self.failure_history=self.failure_history[-20:]

    def to_dict(self)->dict:
        data=asdict(self)
        data["state"]=self.state.value
        data["iteration_stage"]=self.iteration_stage.value
        return data

def utc_now()->str: return datetime.now(timezone.utc).isoformat()

def load_state(path:Path)->AgentState|None:
    if not path.exists(): return None
    data=json.loads(path.read_text(encoding="utf-8"))
    data["state"]=RunState("ITERATION_SUCCEEDED" if data["state"]=="WAITING" else data["state"])
    data["iteration_stage"]=IterationStage(data.get("iteration_stage","IDLE"))
    data.setdefault("branch_name","main")
    data.setdefault("iteration_at_last_rollover",0)
    data.setdefault("failure_history",[])
    data.setdefault("last_ci_result",None)
    data.setdefault("consecutive_no_progress",0)
    data.setdefault("last_progress_result",None)
    data.setdefault("pending_ci_fix",False)
    data.setdefault("last_commit_sha",None)
    data.setdefault("pending_ci_baseline_untracked",[])
    data.setdefault("pending_ci_worktree_fingerprint",None)
    data.setdefault("iteration_started_sha",None)
    data.setdefault("execution_requested",False)
    data.setdefault("execution_applied",False)
    data.setdefault("files_changed",False)
    data.setdefault("meaningful_progress",False)
    data.setdefault("local_ci_ran",False)
    data.setdefault("local_ci_passed",False)
    data.setdefault("commit_created",False)
    data.setdefault("push_verified",False)
    data.setdefault("remote_sha",None)
    data.setdefault("github_ci_verified",False)
    data.setdefault("pending_remote_ci_fix",False)
    data.setdefault("pending_remote_ci_sha",None)
    data.setdefault("pending_remote_ci_result",None)
    return AgentState(**data)

def save_state(path:Path,state:AgentState)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(state.to_dict(),indent=2)+"\n",encoding="utf-8"); tmp.replace(path)

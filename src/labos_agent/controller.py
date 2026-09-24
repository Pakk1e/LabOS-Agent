"""Core controller lifecycle and safety decisions."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from .safety import SafetyLimits,check_limits
from .state import AgentState,RunState

@dataclass
class Controller:
    state:AgentState
    limits:SafetyLimits
    def start(self)->None: self.state.transition(RunState.STARTING)
    def begin_iteration(self,now:datetime)->None:
        decision=check_limits(iteration=self.state.iteration,rollover_count=self.state.rollover_count,
            consecutive_failures=self.state.consecutive_failures,
            consecutive_no_progress=self.state.consecutive_no_progress,
            limits=self.limits,now=now)
        if not decision.allowed: self.stop(decision.reason or "safety limit reached"); return
        self.state.iteration+=1; self.state.transition(RunState.WORKING)
    def mark_waiting(self)->None: self.state.transition(RunState.WAITING)
    def mark_success(self, *, continue_running: bool = True)->None:
        """Record a fully committed/pushed iteration."""
        self.state.consecutive_failures=0
        self.state.consecutive_no_progress=0
        self.state.last_progress_result="progress confirmed"
        if continue_running:
            self.state.transition(
                RunState.WAITING,
                reason="iteration committed and pushed successfully",
            )
        else:
            self.state.transition(
                RunState.COMPLETED,
                reason="iteration committed and pushed successfully",
            )
    def mark_no_progress(self,reason:str)->None:
        self.state.consecutive_no_progress+=1
        self.state.record_failure(reason)
        self.state.last_progress_result=reason
        self.state.transition(RunState.ERROR,reason=reason)
    def mark_failure(self,reason:str)->None:
        self.state.consecutive_failures+=1
        self.state.record_failure(reason)
        self.state.transition(RunState.ERROR,reason=reason)
    def rollover(self)->None:
        self.state.rollover_count+=1
        self.state.iteration_at_last_rollover=self.state.iteration
        self.state.transition(RunState.ROLLOVER,reason="conversation rollover requested")
    def block(self,reason:str)->None: self.state.transition(RunState.BLOCKED,reason=reason)
    def stop(self,reason:str)->None: self.state.transition(RunState.STOPPED,reason=reason)

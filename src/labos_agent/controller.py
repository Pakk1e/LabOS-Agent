"""Core controller lifecycle and safety decisions."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from .safety import SafetyLimits,check_limits
from .state import AgentState,IterationStage,RunState

@dataclass
class Controller:
    state:AgentState
    limits:SafetyLimits

    def start(self)->None:
        self.state.transition(RunState.STARTING)

    def begin_iteration(self,now:datetime)->None:
        decision=check_limits(iteration=self.state.iteration,rollover_count=self.state.rollover_count,
            consecutive_failures=self.state.consecutive_failures,
            consecutive_no_progress=self.state.consecutive_no_progress,
            limits=self.limits,now=now)
        if not decision.allowed:
            self.stop(decision.reason or "safety limit reached")
            return
        self.state.iteration+=1
        self.state.transition(RunState.WORKING)
        self.state.set_iteration_stage(IterationStage.ITERATION_STARTED)

    def chatgpt_working(self)->None:
        self.state.set_iteration_stage(IterationStage.CHATGPT_WORKING)

    def execution_required(self)->None:
        self.state.execution_requested=True
        self.state.set_iteration_stage(IterationStage.EXECUTION_REQUIRED)

    def execution_applied(self)->None:
        self.state.execution_applied=True
        self.state.set_iteration_stage(IterationStage.EXECUTION_APPLIED)

    def progress_verified(self,*,meaningful:bool)->None:
        self.state.files_changed=True
        self.state.meaningful_progress=meaningful
        self.state.last_progress_result = "meaningful progress confirmed" if meaningful else "repository changed but no meaningful implementation progress"
        self.state.set_iteration_stage(IterationStage.PROGRESS_VERIFIED)

    def ci_running(self)->None:
        self.state.local_ci_ran=True
        self.state.set_iteration_stage(IterationStage.CI_RUNNING)

    def ci_passed(self)->None:
        self.state.local_ci_passed=True
        self.state.set_iteration_stage(IterationStage.CI_PASSED)

    def committing(self)->None:
        self.state.set_iteration_stage(IterationStage.COMMITTING)

    def pushing(self)->None:
        self.state.set_iteration_stage(IterationStage.PUSHING)

    def remote_verified(self,sha:str)->None:
        self.state.push_verified=True
        self.state.remote_sha=sha
        self.state.set_iteration_stage(IterationStage.REMOTE_VERIFIED)

    def github_ci_verified(self)->None:
        self.state.github_ci_verified=True
        self.state.set_iteration_stage(IterationStage.ITERATION_SUCCEEDED)

    def mark_success(self, *, continue_running: bool = True)->None:
        """Record success only after the complete evidence chain."""
        if not (
            self.state.iteration_stage == IterationStage.ITERATION_SUCCEEDED
            and self.state.files_changed
            and self.state.meaningful_progress
            and self.state.local_ci_ran
            and self.state.local_ci_passed
            and self.state.commit_created
            and self.state.push_verified
            and self.state.github_ci_verified
        ):
            raise RuntimeError("cannot mark iteration successful before all verification gates pass")
        self.state.consecutive_failures=0
        self.state.consecutive_no_progress=0
        self.state.last_progress_result="progress confirmed"
        reason="iteration committed, pushed, and exact-SHA CI verified"
        if continue_running:
            self.state.transition(RunState.ITERATION_SUCCEEDED,reason=reason)
            self.state.set_iteration_stage(IterationStage.ITERATION_SUCCEEDED)
        else:
            self.state.transition(RunState.COMPLETED,reason=reason)
            self.state.set_iteration_stage(IterationStage.ITERATION_SUCCEEDED)

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

    def block(self,reason:str)->None:
        self.state.transition(RunState.BLOCKED,reason=reason)

    def stop(self,reason:str)->None:
        self.state.transition(RunState.STOPPED,reason=reason)

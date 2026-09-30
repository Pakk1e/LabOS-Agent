import inspect

import labos_agent.supervisor as supervisor


def test_supervisor_snapshots_previous_observed_commit_before_mutating_memory():
    source = inspect.getsource(supervisor.ConversationSupervisor._run_locked)
    snapshot = "previous_observed_commit = memory.last_observed_commit"
    observation = "observed = observe_github(self.project.repository)"
    done_reconciliation = "previous_commit=previous_observed_commit"

    assert snapshot in source
    assert source.index(snapshot) < source.index(observation)
    assert source.index(snapshot) < source.index(done_reconciliation)

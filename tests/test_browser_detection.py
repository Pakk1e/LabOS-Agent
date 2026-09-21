from labos_agent.browser.detection import ResponseObservation

def test_response_observation_is_explicit():
    observation = ResponseObservation(generating=False, input_available=True)
    assert observation.input_available
    assert not observation.generating

def test_response_observation_defaults_are_safe():
    observation = ResponseObservation(generating=False, input_available=False)
    assert not observation.input_available
    assert observation.stop_control_visible is False
    assert observation.assistant_count == 0

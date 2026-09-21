from labos_agent.browser.detection import ResponseObservation

def test_response_observation_is_explicit():
    observation = ResponseObservation(generating=False, input_available=True)
    assert observation.input_available
    assert not observation.generating

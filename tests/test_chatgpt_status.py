from labos_agent.browser.chatgpt import ChatGPTPage


def test_chatgpt_url_detection():
    assert ChatGPTPage._is_chatgpt_url("https://chatgpt.com/")
    assert ChatGPTPage._is_chatgpt_url("https://chatgpt.com/c/abc")
    assert not ChatGPTPage._is_chatgpt_url("https://accounts.google.com/")
    assert not ChatGPTPage._is_chatgpt_url("not-a-url")

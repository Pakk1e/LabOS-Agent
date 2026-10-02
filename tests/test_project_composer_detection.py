from labos_agent.browser.chatgpt import ChatGPTPage



class _EditableCandidate:
    def __init__(self, editable):
        self.editable = editable

    def is_visible(self):
        return True

    def is_editable(self):
        return self.editable

    def evaluate(self, script, expected):
        return False


class _MultipleEditablePage:
    def __init__(self):
        self.candidates = [_EditableCandidate(True), _EditableCandidate(True)]

    def locator(self, selector):
        if selector == '#prompt-textarea[contenteditable="true"], [contenteditable="true"][role="textbox"], [contenteditable="true"]':
            class _Loc:
                def __init__(self, items):
                    self.items = items
                def count(self):
                    return len(self.items)
                def nth(self, index):
                    return self.items[index]
            return _Loc(self.candidates)
        raise AssertionError(f"unexpected selector: {selector}")


def test_project_composer_accepts_multiple_visible_editable_surfaces():
    chat = ChatGPTPage(_MultipleEditablePage())
    assert chat.project_chat_composer("STEM_Studio") is not None

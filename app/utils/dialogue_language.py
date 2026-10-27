"""Explicit dialect requests shared by generation and soundtrack verification."""
import re


def requests_cantonese(*values):
    text = "。".join(str(value or "") for value in values)
    sentences = re.split(r"[。！？;\n]", text)
    for sentence in sentences:
        if not re.search(r"粤语|粵語|广东话|廣東話|cantonese", sentence, re.I):
            continue
        if re.search(r"(?:不要|不用|无需|無需|禁止|不使用)\s*(?:粤语|粵語|广东话|廣東話)|"
                     r"(?:no|without|not)\s+cantonese", sentence, re.I):
            continue
        return True
    return False


def requests_english_dialogue(*values):
    text = "。".join(str(value or "") for value in values)
    for sentence in re.split(r"[。！？;\n]", text):
        if re.search(r"(?:不要|不用|禁止|不使用)\s*(?:英语|英語|英文)|"
                     r"(?:no|without|not)\s+english", sentence, re.I):
            continue
        if re.search(r"(?:英语|英語|英文)\s*(?:对白|對白|对话|對話|台词|臺詞|配音|旁白)|"
                     r"english\s+(?:dialogue|dialog|speech|voice|voiceover)|"
                     r"(?:dialogue|speech|voiceover)\s+(?:in\s+)?english", sentence, re.I):
            return True
    return False


def dialogue_language_instruction(*values):
    if not requests_english_dialogue(*values):
        return ""
    return (
        "SPOKEN LANGUAGE LOCK: The user explicitly requests English dialogue. "
        "Every actual spoken utterance in scenes.dialogue must be written and performed "
        "in natural English. Keep [speaker names] and descriptive fields in the UI language "
        "if needed, but the UI/document language must never translate the spoken lines "
        "into Chinese. English dialogue has priority over the document output-language rule. "
        "Any quoted spoken line in descriptions must match the English dialogue verbatim. "
        "Silent scenes remain silent; do not add dialogue merely to demonstrate the language."
    )

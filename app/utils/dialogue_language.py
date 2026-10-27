"""Explicit dialect requests shared by generation and soundtrack verification."""
import re


def language_key(value):
    """Compare common names without conflating Mandarin with Cantonese."""
    text = str(value or "").strip().lower()
    for key, pattern in (
        ("cantonese", r"粤|粵|广东话|廣東話|cantonese|^yue"),
        ("mandarin", r"普通|国语|國語|mandarin|^中文$|^chinese$|^zh(?:-cn)?$"),
        ("english", r"英语|英語|英文|english|^en(?:-|$)"),
        ("japanese", r"日语|日語|日文|japanese|^ja(?:-|$)"),
    ):
        if re.search(pattern, text):
            return key
    return text


def character_language_instruction(characters):
    lines = []
    for character in characters or []:
        get = character.get if isinstance(character, dict) else lambda k, d=None: getattr(character, k, d)
        language = get("dialogue_language")
        if not language:
            continue
        dialect = get("dialogue_dialect") or ""
        mixing = ("Natural English code-switching is allowed; keep the character's base "
                  "language and dialect unchanged." if get("allow_english_mixing", True)
                  else "No language mixing.")
        lines.append(f"[{get('name')}]: {language}; dialect/accent: {dialect or 'standard'}. {mixing}")
    return (
        "PER-CHARACTER SPOKEN LANGUAGE LOCK (highest priority for each speaker):\n"
        + "\n".join(lines)
        + "\nUse the same base language, dialect and voice in EVERY scene and edit. "
        "Mandarin and Cantonese are distinct: never switch between them. A language "
        "assigned to one speaker must not spread to another. Written Chinese or the "
        "document/UI language does not change pronunciation. Speak only scripted lines."
    ) if lines else ""


def script_language_feedback(data, user_input):
    """Record explicit conflicts before aligning scene declarations to character locks."""
    feedback = []
    characters = data.get("characters") or []
    for character in characters:
        if language_key(character.get("dialogue_dialect")) == "cantonese":
            character["dialogue_language"] = "粤语"
        if not character.get("dialogue_language"):
            feedback.append(f"角色 [{character['name']}] 缺少固定 dialogue_language；定义对白语言和方言。")
            hints = " ".join(str(character.get(k) or "") for k in ("voice_features", "voice_style"))
            character["dialogue_language"] = (
                "粤语" if requests_cantonese(hints) else
                "English" if requests_english_dialogue(hints, user_input) else "中文普通话")
        if language_key(character["dialogue_language"]) == "cantonese" and not character.get("dialogue_dialect"):
            character["dialogue_dialect"] = "粤语"
    mapping = {c["name"]: c for c in characters}
    for scene in data.get("scenes") or []:
        declarations = scene.get("dialogue_languages") or {}
        expected = {}
        for name in scene.get("characters_present") or []:
            character = mapping.get(name)
            if not character:
                continue
            base = character["dialogue_language"]
            spoken = declarations.get(name)
            if spoken and language_key(spoken) != language_key(base):
                # English is a permitted secondary language, never a new base lock.
                if not (language_key(spoken) == "english" and character.get("allow_english_mixing", True)):
                    feedback.append(f"分镜 {scene['scene_number']} 的 [{name}] 使用 {spoken}，"
                                    f"与固定语言 {base} 冲突；统一对白文字、发音与方言。")
            expected[name] = base
        scene["dialogue_languages"] = expected
    return feedback


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
        "If the user assigns different languages to named characters, apply English only "
        "to those English-speaking characters and preserve the other character locks. "
        "Every actual spoken utterance in scenes.dialogue must be written and performed "
        "in natural English. Keep [speaker names] and descriptive fields in the UI language "
        "if needed, but the UI/document language must never translate the spoken lines "
        "into Chinese. English dialogue has priority over the document output-language rule. "
        "Any quoted spoken line in descriptions must match the English dialogue verbatim. "
        "Silent scenes remain silent; do not add dialogue merely to demonstrate the language."
    )

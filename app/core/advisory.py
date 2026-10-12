"""Section 56: the engine advises; people decide. One wording, used everywhere output could be mistaken for an instruction."""
ADVISORY = {
    "en": "This is decision support based on the data connected to the system. It is not an instruction or a guarantee; "
          "you decide what to do.",
    "sw": "Hii ni msaada wa kufanya maamuzi kulingana na data iliyounganishwa kwenye mfumo. Si agizo wala hakikisho; "
          "uamuzi ni wako.",
}
LANGUAGE_NAMES = {"en": "English", "sw": "Swahili"}


def advisory_text(lang: str = "en") -> str:
    return ADVISORY.get(lang, ADVISORY["en"])


def language_name(lang: str) -> str:
    return LANGUAGE_NAMES.get(lang, "English")

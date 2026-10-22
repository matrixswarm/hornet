"""Conservative, deterministic text cleanup; no authorship detection claims."""
import re
import unicodedata


def clean_text(text):
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    return re.sub(r"\n{4,}", "\n\n\n", text).strip("\n") + "\n"

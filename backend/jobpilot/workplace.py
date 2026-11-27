"""One conservative workplace classifier for search and eligibility checks."""
import re


def classify(location: str, description: str = "", title: str = "") -> str:
    # Feed metadata encoded in location takes precedence over company boilerplate.
    explicit = f"{location}\n{title}".casefold()
    if re.search(r"\bhybrid\b|гибрид", explicit):
        return "hybrid"
    if re.search(r"\bon[ -]?site\b|\bin[ -]office\b|\boffice[ -]based\b|\bофис", explicit):
        return "onsite"
    if re.search(r"\bremote\b|удал[её]н|\bhome[ -]office\b", explicit):
        return "remote"
    text = description.casefold()
    # Mandatory office attendance wins over a remote benefit elsewhere in the text.
    if re.search(r"\bhybrid\b|гибрид|\b\d+\s+days?\s+(?:a week\s+)?(?:in|at)\s+(?:the\s+)?office\b", text):
        return "hybrid"
    if re.search(r"\b(?:not (?:a )?remote|no remote (?:work|working)|"
                 r"(?:this |the )?(?:role|position|job) (?:is|will be) (?:fully )?on[ -]?site|"
                 r"office[ -]based (?:role|position)|work (?:from|in) (?:our|the) office)\b|"
                 r"(?:работа|работать)\s+(?:только\s+)?(?:в|из)\s+офис", text):
        return "onsite"
    if re.search(r"\b(?:fully remote|100% remote|remote[ -](?:first|only)|"
                 r"(?:this |the )?(?:role|position|job) (?:is|will be|can be) (?:fully )?remote|"
                 r"remote (?:role|position|job)|work(?:ing)? (?:remotely|from home|from anywhere))\b|"
                 r"(?:полностью\s+удал[её]н|удал[её]нн\w*\s+(?:работа|формат))", text):
        return "remote"
    return ""

"""Escaping for LinkedIn's "little text format" (the /rest/posts commentary field).

Unescaped reserved characters — parentheses and brackets above all — are the
classic cause of opaque 400 INVALID errors from the Posts API. We escape every
reserved character except '#' and '@', so hashtags keep working and everything
else renders as literal text. The legacy ugcPosts backend takes plain text and
must NOT be escaped.
"""

_RESERVED = set("\\|{}[]()<>*_~")


def escape_little_text(text: str) -> str:
    return "".join("\\" + ch if ch in _RESERVED else ch for ch in text)

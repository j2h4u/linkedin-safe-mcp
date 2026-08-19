from linkedin_mcp.api.ltf import escape_little_text


def test_reserved_characters_escaped():
    assert escape_little_text("Hi (world) [ok] {x} <y> a|b *c* _d_ ~e~") == (
        "Hi \\(world\\) \\[ok\\] \\{x\\} \\<y\\> a\\|b \\*c\\* \\_d\\_ \\~e\\~"
    )


def test_backslash_escaped_first_class():
    assert escape_little_text("a\\b") == "a\\\\b"


def test_hashtags_and_mentions_left_alone():
    assert escape_little_text("Shipping #AI today @ noon") == "Shipping #AI today @ noon"


def test_plain_text_unchanged():
    text = "Just shipped a new release! Details soon."
    assert escape_little_text(text) == text

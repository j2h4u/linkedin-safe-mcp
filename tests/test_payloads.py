from linkedin_mcp.api.client import build_rest_post_payload, build_ugc_post_payload

AUTHOR = "urn:li:person:abc123"


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def test_rest_text_post():
    payload = _dict(build_rest_post_payload(AUTHOR, "Hello (world)", "PUBLIC"))
    assert payload["author"] == AUTHOR
    assert payload["commentary"] == "Hello \\(world\\)"  # little-text escaping applied
    assert payload["visibility"] == "PUBLIC"
    assert payload["lifecycleState"] == "PUBLISHED"
    assert _dict(payload["distribution"])["feedDistribution"] == "MAIN_FEED"
    assert "content" not in payload


def test_rest_article_post_title_falls_back_to_url():
    payload = _dict(
        build_rest_post_payload(AUTHOR, "Read this", "PUBLIC", article={"url": "https://x.test/a", "title": None})
    )
    article = _dict(_dict(payload["content"])["article"])
    assert article["source"] == "https://x.test/a"
    assert article["title"] == "https://x.test/a"
    assert "description" not in article


def test_rest_image_post():
    payload = _dict(build_rest_post_payload(AUTHOR, "pic", "CONNECTIONS", image_urn="urn:li:image:z1"))
    assert _dict(_dict(payload["content"])["media"])["id"] == "urn:li:image:z1"


def test_ugc_text_post_not_escaped():
    payload = _dict(build_ugc_post_payload(AUTHOR, "Hello (world)", "PUBLIC"))
    content = _dict(_dict(payload["specificContent"])["com.linkedin.ugc.ShareContent"])
    assert _dict(content["shareCommentary"])["text"] == "Hello (world)"  # ugc takes plain text
    assert content["shareMediaCategory"] == "NONE"
    assert _dict(payload["visibility"])["com.linkedin.ugc.MemberNetworkVisibility"] == "PUBLIC"


def test_ugc_article_post():
    payload = _dict(
        build_ugc_post_payload(
            AUTHOR,
            "Read",
            "PUBLIC",
            article={"url": "https://x.test/a", "title": "T", "description": "D"},
        )
    )
    content = _dict(_dict(payload["specificContent"])["com.linkedin.ugc.ShareContent"])
    assert content["shareMediaCategory"] == "ARTICLE"
    media = _dict(_list(content["media"])[0])
    assert media == {
        "status": "READY",
        "originalUrl": "https://x.test/a",
        "title": {"text": "T"},
        "description": {"text": "D"},
    }


def test_ugc_image_post():
    payload = _dict(build_ugc_post_payload(AUTHOR, "pic", "PUBLIC", image_asset="urn:li:digitalmediaAsset:q"))
    content = _dict(_dict(payload["specificContent"])["com.linkedin.ugc.ShareContent"])
    assert content["shareMediaCategory"] == "IMAGE"
    assert _dict(_list(content["media"])[0])["media"] == "urn:li:digitalmediaAsset:q"

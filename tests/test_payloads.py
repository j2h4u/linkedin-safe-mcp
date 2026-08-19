from linkedin_mcp.api.client import build_rest_post_payload, build_ugc_post_payload

AUTHOR = "urn:li:person:abc123"


def test_rest_text_post():
    payload = build_rest_post_payload(AUTHOR, "Hello (world)", "PUBLIC")
    assert payload["author"] == AUTHOR
    assert payload["commentary"] == "Hello \\(world\\)"  # little-text escaping applied
    assert payload["visibility"] == "PUBLIC"
    assert payload["lifecycleState"] == "PUBLISHED"
    assert payload["distribution"]["feedDistribution"] == "MAIN_FEED"
    assert "content" not in payload


def test_rest_article_post_title_falls_back_to_url():
    payload = build_rest_post_payload(
        AUTHOR, "Read this", "PUBLIC", article={"url": "https://x.test/a", "title": None}
    )
    article = payload["content"]["article"]
    assert article["source"] == "https://x.test/a"
    assert article["title"] == "https://x.test/a"
    assert "description" not in article


def test_rest_image_post():
    payload = build_rest_post_payload(AUTHOR, "pic", "CONNECTIONS", image_urn="urn:li:image:z1")
    assert payload["content"]["media"]["id"] == "urn:li:image:z1"


def test_ugc_text_post_not_escaped():
    payload = build_ugc_post_payload(AUTHOR, "Hello (world)", "PUBLIC")
    content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareCommentary"]["text"] == "Hello (world)"  # ugc takes plain text
    assert content["shareMediaCategory"] == "NONE"
    assert payload["visibility"]["com.linkedin.ugc.MemberNetworkVisibility"] == "PUBLIC"


def test_ugc_article_post():
    payload = build_ugc_post_payload(
        AUTHOR,
        "Read",
        "PUBLIC",
        article={"url": "https://x.test/a", "title": "T", "description": "D"},
    )
    content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareMediaCategory"] == "ARTICLE"
    media = content["media"][0]
    assert media == {
        "status": "READY",
        "originalUrl": "https://x.test/a",
        "title": {"text": "T"},
        "description": {"text": "D"},
    }


def test_ugc_image_post():
    payload = build_ugc_post_payload(
        AUTHOR, "pic", "PUBLIC", image_asset="urn:li:digitalmediaAsset:q"
    )
    content = payload["specificContent"]["com.linkedin.ugc.ShareContent"]
    assert content["shareMediaCategory"] == "IMAGE"
    assert content["media"][0]["media"] == "urn:li:digitalmediaAsset:q"

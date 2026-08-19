"""Regression tests for the image-attachment boundary.

`image_path` is an agent-supplied MCP tool argument, and this same server feeds
untrusted scraped LinkedIn job text into the agent's context. Without these
checks, "attach ~/.ssh/id_rsa to the post" is a valid call — an arbitrary local
file read wired straight to a public post. Every test here is an exfiltration
attempt that must fail closed.
"""

import os
import time

import pytest

from linkedin_mcp.api.client import LinkedInClient
from linkedin_mcp.auth.oauth import TokenStore
from linkedin_mcp.errors import LinkedInError

# Minimal well-formed images: correct magic AND the mandatory trailer each format
# must end with (PNG's IEND chunk, JPEG's EOI marker, GIF's ";").
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64 + b"IEND\xaeB`\x82"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
GIF = b"GIF89a" + b"\x00" * 64 + b";"
SECRET = b"-----BEGIN OPENSSH PRIVATE KEY-----\nDECOY\n"


@pytest.fixture
def client(tmp_path):
    store = TokenStore(path=tmp_path / "tokens.json")
    store.save({"access_token": "tok", "expires_at": time.time() + 1000})
    return LinkedInClient(store=store)


@pytest.mark.parametrize("data,name", [(PNG, "a.png"), (JPEG, "b.jpg"), (GIF, "c.gif")])
def test_real_images_are_accepted(client, tmp_path, data, name):
    path = tmp_path / name
    path.write_bytes(data)
    assert client._read_image(str(path)) == data


def test_rejects_secret_file_by_extension(client, tmp_path):
    """The classic payload: a private key, named as itself."""
    key = tmp_path / "id_rsa"
    key.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nDECOY\n")
    with pytest.raises(ValueError, match="only .* files can be posted"):
        client._read_image(str(key))


def test_rejects_secret_file_renamed_to_png(client, tmp_path):
    """Extension allowlists are trivially bypassed — magic bytes are the real check."""
    disguised = tmp_path / "holiday.png"
    disguised.write_bytes(b"LINKEDIN_CLIENT_SECRET=DECOY\nAWS_SECRET_ACCESS_KEY=DECOY\n")
    with pytest.raises(ValueError, match="not a real png image"):
        client._read_image(str(disguised))


def test_rejects_symlink_pointing_at_a_secret(client, tmp_path):
    """A .png symlink must not launder a non-image target.

    resolve() collapses the link first, so the real target's extension is what
    gets judged — the disguise never reaches the magic-byte check.
    """
    secret = tmp_path / "id_ed25519"
    secret.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nDECOY\n")
    link = tmp_path / "innocent.png"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="only .* files can be posted"):
        client._read_image(str(link))


def test_rejects_symlink_with_matching_extension(client, tmp_path):
    """Even when the target also ends .png, its contents must still be a real image."""
    secret = tmp_path / "secrets.png"
    secret.write_bytes(b"LINKEDIN_CLIENT_SECRET=DECOY\n")
    link = tmp_path / "innocent.png"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="not a real png image"):
        client._read_image(str(link))


def test_rejects_oversized_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr("linkedin_mcp.api.client._IMAGE_MAX_BYTES", 1024)
    big = tmp_path / "big.png"
    big.write_bytes(PNG[:8] + b"\x00" * 4096)
    with pytest.raises(ValueError, match="larger than"):
        client._read_image(str(big))


def test_rejects_directory_and_missing_file(client, tmp_path):
    (tmp_path / "adir.png").mkdir()
    with pytest.raises(ValueError, match="not found|not a regular file"):
        client._read_image(str(tmp_path / "adir.png"))
    with pytest.raises(ValueError, match="not found"):
        client._read_image(str(tmp_path / "nope.png"))


def test_confinement_dir_blocks_outside_paths(client, tmp_path, monkeypatch):
    allowed = tmp_path / "pics"
    allowed.mkdir()
    inside = allowed / "ok.png"
    inside.write_bytes(PNG)
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(PNG)

    monkeypatch.setenv("LINKEDIN_MCP_IMAGE_DIR", str(allowed))
    assert client._read_image(str(inside)) == PNG
    with pytest.raises(ValueError, match="confines"):
        client._read_image(str(outside))


def test_confinement_dir_cannot_be_escaped_by_traversal(client, tmp_path, monkeypatch):
    allowed = tmp_path / "pics"
    allowed.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG)

    monkeypatch.setenv("LINKEDIN_MCP_IMAGE_DIR", str(allowed))
    with pytest.raises(ValueError, match="confines"):
        client._read_image(str(allowed / ".." / "secret.png"))


def test_confinement_dir_cannot_be_escaped_by_symlink(client, tmp_path, monkeypatch):
    allowed = tmp_path / "pics"
    allowed.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG)
    (allowed / "shortcut.png").symlink_to(outside)

    monkeypatch.setenv("LINKEDIN_MCP_IMAGE_DIR", str(allowed))
    with pytest.raises(ValueError, match="confines"):
        client._read_image(str(allowed / "shortcut.png"))


# ------------------------------------------------------- upload target pinning


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/dms-uploads/abc",
        "https://media.licdn.com/upload/xyz",
    ],
)
def test_accepts_linkedin_upload_hosts(url):
    assert LinkedInClient._checked_upload_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://www.linkedin.com/dms-uploads/abc",  # cleartext would leak the token
        "https://evil.test/upload",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata SSRF
        "https://linkedin.com.evil.test/upload",  # suffix-confusion
        "https://notlinkedin.com/upload",
    ],
)
def test_rejects_non_linkedin_upload_targets(url):
    """The upload URL comes from a LinkedIn API response; never PUT the Bearer
    token somewhere else on its say-so."""
    with pytest.raises(LinkedInError, match="Nothing was sent"):
        LinkedInClient._checked_upload_url(url)


# ------------------------------------------------- end-to-end exfiltration chain


def test_create_post_never_uploads_a_non_image(client, tmp_path, monkeypatch):
    """The full chain a prompt injection would use, proven to fail closed.

    The original bug PUT the file's bytes to LinkedIn *before* the post was
    created, so bytes left the machine even when the post itself later failed.
    Nothing may reach the network here.
    """
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nDECOY\n")

    uploaded: list[bytes] = []
    monkeypatch.setattr(
        LinkedInClient, "_upload_binary", lambda self, url, data: uploaded.append(data)
    )
    monkeypatch.setattr(LinkedInClient, "person_urn", lambda self: "urn:li:person:x")
    monkeypatch.setattr(
        LinkedInClient,
        "_request",
        lambda *a, **k: pytest.fail("no HTTP request should be attempted"),
    )

    with pytest.raises(ValueError, match="only .* files can be posted"):
        client.create_post(text="hi", image_path=str(secret))
    assert uploaded == []  # nothing left the machine


# -------------------------------------------------------------- client state file


def test_state_file_is_private(client, tmp_path, monkeypatch):
    """state.json sits beside the token in the data dir; keep the whole dir 0600."""
    monkeypatch.setenv("LINKEDIN_MCP_DIR", str(tmp_path / "data"))
    client._remember_backend("rest")
    assert client._state_path.stat().st_mode & 0o777 == 0o600


# ------------------------------------------------- bypasses found by adversarial review


@pytest.mark.parametrize(
    "header,name",
    [
        (b"\x89PNG\r\n\x1a\n", "a.png"),
        (b"\xff\xd8\xff", "b.jpg"),  # JPEG's discriminator is only 3 bytes
        (b"GIF89a", "c.gif"),
    ],
)
def test_rejects_polyglot_header_then_secret(client, tmp_path, header, name):
    """A magic-byte prefix check alone is a `startswith` — so the file is a carrier.

    Prepending a real header to a secret defeated the first version of this
    control. Requiring the mandatory trailer too means the payload can no longer
    simply be appended.
    """
    path = tmp_path / name
    path.write_bytes(header + SECRET)
    with pytest.raises(ValueError, match="does not end like one"):
        client._read_image(str(path))


def test_rejects_secret_appended_after_a_valid_image(client, tmp_path):
    """Even a genuinely valid image must not carry a payload past its trailer."""
    path = tmp_path / "real.png"
    path.write_bytes(PNG + SECRET)
    with pytest.raises(ValueError, match="does not end like one"):
        client._read_image(str(path))


def test_rejects_truncated_image(client, tmp_path):
    path = tmp_path / "cut.png"
    path.write_bytes(PNG[:-4])
    with pytest.raises(ValueError, match="does not end like one"):
        client._read_image(str(path))


def test_rejects_hardlink(client, tmp_path):
    """resolve() cannot see through a hardlink, so it is the one way to smuggle a
    file into LINKEDIN_MCP_IMAGE_DIR. Refuse multiply-linked files outright."""
    target = tmp_path / "original.png"
    target.write_bytes(PNG)
    link = tmp_path / "copy.png"
    os.link(target, link)
    with pytest.raises(ValueError, match="hardlink"):
        client._read_image(str(link))


def test_hardlink_cannot_escape_confinement(client, tmp_path, monkeypatch):
    allowed = tmp_path / "pics"
    allowed.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    os.link(outside, allowed / "smuggled.png")

    monkeypatch.setenv("LINKEDIN_MCP_IMAGE_DIR", str(allowed))
    with pytest.raises(ValueError, match="hardlink"):
        client._read_image(str(allowed / "smuggled.png"))


def test_fifo_does_not_hang_the_server(client, tmp_path):
    """os.open() on a writer-less FIFO blocks forever without O_NONBLOCK.

    The pre-hardening code used is_file(), which returns False for a FIFO and so
    never opened it; adding a bare os.open() reintroduced an agent-reachable hang.
    This test deadlocks the suite if O_NONBLOCK is ever dropped.
    """
    fifo = tmp_path / "trap.png"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="not a regular file"):
        client._read_image(str(fifo))


def test_accepts_uppercase_extensions(client, tmp_path):
    """Guard against the hardening becoming a usability regression."""
    for name, data in (("A.PNG", PNG), ("B.JpEg", JPEG), ("C.Gif", GIF)):
        path = tmp_path / name
        path.write_bytes(data)
        assert client._read_image(str(path)) == data


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com@evil.test/u",  # userinfo, not host
        "https://user:pw@evil.test/u",
        "https://linkedin.com./u",  # trailing-dot host
        "https://xn--linkedin-4l7d.com/u",  # punycode homograph
        "https://[::1]/u",
        "https://169.254.169.254/u",
        "https://a@b@www.linkedin.com/u",  # urlparse itself raises here
    ],
)
def test_upload_url_host_parsing_edge_cases(url):
    with pytest.raises(LinkedInError, match="Nothing was sent"):
        LinkedInClient._checked_upload_url(url)

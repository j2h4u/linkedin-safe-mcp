"""Exception types shared across the package.

Error messages are written for the AI agent that receives them as tool errors:
they state what failed and what action would fix it.
"""


class LinkedInError(Exception):
    """Base class for all failures raised by this package."""


class NotAuthenticatedError(LinkedInError):
    """No valid access token. The fix is always to (re-)run the OAuth flow."""


class LinkedInAPIError(LinkedInError):
    """A non-2xx response from api.linkedin.com."""

    def __init__(self, status: int, message: str, code: int | None = None):
        self.status = status
        self.code = code
        super().__init__(f"LinkedIn API error {status}: {message}")


class RateLimitedError(LinkedInError):
    """LinkedIn is throttling anonymous guest requests from this IP."""

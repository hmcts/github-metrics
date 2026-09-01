"""Test how a run's GitHub credential is resolved, minted and renewed."""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from requests import ConnectionError as RequestsConnectionError

from metrics.credentials import (
    AppInstallation,
    CredentialsError,
    PersonalAccessToken,
    key_configured,
    private_key,
    redacted,
    resolve_credentials,
)

EXPIRES_AT = "2026-09-01T12:00:00Z"
NOW = datetime(2026, 9, 1, 11, 0, tzinfo=UTC)
"""A fixed instant an hour before the fixture expiry, injected everywhere as the clock.

The real clock is deliberately not used: `EXPIRES_AT` is a date the machine running these tests will
one day be past, and a token the fixtures treat as fresh would then be re-minted on every read —
turning every cached-hit assertion into a failure nobody changed the code to cause.
"""


@pytest.fixture(scope="session")
def signing_key() -> tuple[str, str]:
    """Return a throwaway RSA key pair as PEM text, private first, so RS256 can really be verified."""
    generated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = generated.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        generated.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


def claims(headers: Mapping[str, str], public_key: str) -> dict[str, Any]:
    """Verify the RS256 signature on the assertion that was sent, and return what it claimed.

    Expiry verification is off because the JWT is signed from `NOW` rather than from the real clock,
    so PyJWT would read a fixture instant in the past as an expired token. The claims themselves are
    asserted against `NOW` instead, which is what the tests are about.
    """
    token = headers["Authorization"].removeprefix("Bearer ")
    decoded: dict[str, Any] = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_exp": False})
    return decoded


def exchange(payload: object, status_code: int = 201, text: str = "") -> MagicMock:
    """Build one stubbed answer to the installation token exchange."""
    response = MagicMock(status_code=status_code, ok=status_code < 400, text=text)
    response.json.return_value = payload
    return response


def installation(
    key: str,
    *responses: object,
    now: datetime = NOW,
    waits: list[float] | None = None,
) -> tuple[AppInstallation, MagicMock]:
    """Build an installation credential whose session answers with the given responses in turn.

    A response may be an exception, which `side_effect` raises, so a transport failure is set up the
    same way a refusal is. The pause is recorded rather than taken, so a bounded retry costs the
    suite no wall-clock time.
    """
    session = MagicMock()
    session.post.side_effect = responses
    recorded = waits if waits is not None else []
    credentials = AppInstallation(1234, 5678, key, session, clock=lambda: now, pause=recorded.append)
    return credentials, session.post


def test_personal_access_token_returns_the_configured_value() -> None:
    """Send back exactly the token the environment set."""
    credentials = PersonalAccessToken("secret")

    assert credentials.token() == "secret"


def test_personal_access_token_has_nothing_to_refresh() -> None:
    """Report that a refused PAT cannot be replaced, so a 401 is not retried with the same string."""
    assert PersonalAccessToken("secret").refresh() is False


def test_resolve_credentials_selects_the_app_when_it_is_fully_configured() -> None:
    """Prefer App auth when an id, an installation and a key are all present."""
    environment = {
        "GH_APP_ID": "1234",
        "GH_APP_INSTALLATION_ID": "5678",
        "GH_APP_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----",
        "GH_TOKEN": "secret",
    }

    credentials = resolve_credentials(MagicMock(), environment)

    assert isinstance(credentials, AppInstallation)
    assert credentials.app_identifier == 1234
    assert credentials.installation_identifier == 5678


def test_resolve_credentials_reads_the_key_from_a_path(tmp_path: Path) -> None:
    """Read the private key off disk when a path names one."""
    key = tmp_path / "app.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nfrom-disk\n", encoding="utf-8")
    environment = {
        "GH_APP_ID": "1234",
        "GH_APP_INSTALLATION_ID": "5678",
        "GH_APP_PRIVATE_KEY_PATH": str(key),
    }

    credentials = resolve_credentials(MagicMock(), environment)

    assert isinstance(credentials, AppInstallation)
    assert credentials.private_key == "-----BEGIN PRIVATE KEY-----\nfrom-disk\n"


def test_private_key_prefers_the_path_to_the_inline_value(tmp_path: Path) -> None:
    """Take the key from disk when both sources are set, because the file is the safer one."""
    key = tmp_path / "app.pem"
    key.write_text("from-disk", encoding="utf-8")

    resolved = private_key({"GH_APP_PRIVATE_KEY_PATH": str(key), "GH_APP_PRIVATE_KEY": "inline"})

    assert resolved == "from-disk"


def test_private_key_restores_escaped_newlines() -> None:
    """Turn a one-line PEM from a CI secret store back into something that will parse."""
    resolved = private_key({"GH_APP_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\\nbody\\n-----END-----"})

    assert resolved == "-----BEGIN PRIVATE KEY-----\nbody\n-----END-----"


def test_key_configured_is_false_when_neither_variable_is_set() -> None:
    """Report the App as a half-set when no key source is named, so the run falls back to the token."""
    assert key_configured({}) is False


def test_key_configured_is_true_for_either_source() -> None:
    """Treat a named key as configured whichever variable named it, before anything is read."""
    assert key_configured({"GH_APP_PRIVATE_KEY_PATH": "/app.pem"}) is True
    assert key_configured({"GH_APP_PRIVATE_KEY": "inline"}) is True


def test_private_key_reports_an_empty_file(tmp_path: Path) -> None:
    """Name the empty file, rather than letting the signer call it an unparseable public key."""
    key = tmp_path / "app.pem"
    key.write_text("", encoding="utf-8")

    with pytest.raises(CredentialsError, match=f"private key read from {key} is empty"):
        private_key({"GH_APP_PRIVATE_KEY_PATH": str(key)})


def test_private_key_reports_a_blank_inline_value() -> None:
    """Name the variable when the key set inline is whitespace, which a broken CI secret produces."""
    with pytest.raises(CredentialsError, match="private key in GH_APP_PRIVATE_KEY is empty"):
        private_key({"GH_APP_PRIVATE_KEY": "  \n  "})


def test_private_key_reports_an_unreadable_path(tmp_path: Path) -> None:
    """Name the path that could not be read, because a typo there is the likeliest cause."""
    missing = tmp_path / "absent.pem"

    with pytest.raises(CredentialsError, match=str(missing)):
        private_key({"GH_APP_PRIVATE_KEY_PATH": str(missing)})


def test_private_key_reports_a_file_that_is_not_text(tmp_path: Path) -> None:
    """Name the path for a key that is not UTF-8, rather than ending the run in a decode traceback."""
    key = tmp_path / "app.der"
    key.write_bytes(b"\x30\x82\x04\xa4\x02\x01\x00\xff\xfe")

    with pytest.raises(CredentialsError, match=f"could not be read from {key}"):
        private_key({"GH_APP_PRIVATE_KEY_PATH": str(key)})


def test_private_key_expands_a_home_relative_path(tmp_path: Path) -> None:
    """Read a `~` path, because the variable does not always arrive from a shell that expanded it."""
    key = tmp_path / "app.pem"
    key.write_text("from-home", encoding="utf-8")

    with patch.dict("os.environ", {"HOME": str(tmp_path)}):
        assert private_key({"GH_APP_PRIVATE_KEY_PATH": "~/app.pem"}) == "from-home"


def test_private_key_reports_a_home_that_cannot_be_resolved() -> None:
    """Name the path rather than raising a bare RuntimeError when `~` has no home to expand to."""
    failure = RuntimeError("Could not determine home directory.")

    with (
        patch("metrics.credentials.Path.expanduser", side_effect=failure),
        pytest.raises(CredentialsError, match=r"could not be read from ~/app\.pem"),
    ):
        private_key({"GH_APP_PRIVATE_KEY_PATH": "~/app.pem"})


def test_resolve_credentials_falls_back_to_the_token() -> None:
    """Use the PAT when no App is configured."""
    credentials = resolve_credentials(MagicMock(), {"GH_TOKEN": "secret"})

    assert isinstance(credentials, PersonalAccessToken)
    assert credentials.token() == "secret"


def test_resolve_credentials_falls_back_when_the_app_is_half_configured() -> None:
    """Fall back rather than fail when a leftover App variable is set on its own."""
    environment = {"GH_APP_ID": "1234", "GH_TOKEN": "secret"}

    assert isinstance(resolve_credentials(MagicMock(), environment), PersonalAccessToken)


def test_resolve_credentials_ignores_a_stale_key_path_when_the_app_is_half_configured(tmp_path: Path) -> None:
    """Fall back on a leftover key path alone, rather than reading it and failing a token-only run."""
    environment = {"GH_APP_PRIVATE_KEY_PATH": str(tmp_path / "deleted.pem"), "GH_TOKEN": "secret"}

    assert isinstance(resolve_credentials(MagicMock(), environment), PersonalAccessToken)


def test_resolve_credentials_fails_when_a_configured_app_names_an_unreadable_key(tmp_path: Path) -> None:
    """Stop a fully configured App whose key is missing, rather than downgrading it to the token."""
    missing = tmp_path / "absent.pem"
    environment = {
        "GH_APP_ID": "1234",
        "GH_APP_INSTALLATION_ID": "5678",
        "GH_APP_PRIVATE_KEY_PATH": str(missing),
        "GH_TOKEN": "secret",
    }

    with pytest.raises(CredentialsError, match=f"could not be read from {missing}"):
        resolve_credentials(MagicMock(), environment)


def test_resolve_credentials_fails_when_a_configured_app_names_an_empty_key(tmp_path: Path) -> None:
    """Refuse an empty PEM outright, because falling back here loses the permissions App auth is for."""
    key = tmp_path / "app.pem"
    key.write_text("", encoding="utf-8")
    environment = {
        "GH_APP_ID": "1234",
        "GH_APP_INSTALLATION_ID": "5678",
        "GH_APP_PRIVATE_KEY_PATH": str(key),
        "GH_TOKEN": "secret",
    }

    with pytest.raises(CredentialsError, match="is empty"):
        resolve_credentials(MagicMock(), environment)


def test_resolve_credentials_rejects_a_non_numeric_app_id() -> None:
    """Say which variable is wrong, rather than letting GitHub answer 401 about it later."""
    environment = {
        "GH_APP_ID": "Iv1.abc123",
        "GH_APP_INSTALLATION_ID": "5678",
        "GH_APP_PRIVATE_KEY": "key",
    }

    with pytest.raises(CredentialsError, match="GH_APP_ID must be a number"):
        resolve_credentials(MagicMock(), environment)


def test_resolve_credentials_reports_nothing_configured() -> None:
    """Name both ways of authenticating when the environment offers neither."""
    with pytest.raises(CredentialsError, match="no GitHub credentials are configured") as captured:
        resolve_credentials(MagicMock(), {})

    assert "GH_APP_ID" in str(captured.value)
    assert "GH_TOKEN" in str(captured.value)


def test_resolve_credentials_reads_the_process_environment_by_default() -> None:
    """Default to `os.environ`, the way `sonar_token`'s caller does."""
    with patch.dict("os.environ", {"GH_TOKEN": "from-process"}, clear=True):
        credentials = resolve_credentials(MagicMock())

    assert credentials.token() == "from-process"


def test_app_installation_mints_a_token(signing_key: tuple[str, str]) -> None:
    """Exchange a signed JWT for an installation token, and hold the expiry GitHub reports."""
    private, public = signing_key
    credentials, post = installation(private, exchange({"token": "ghs_minted", "expires_at": EXPIRES_AT}))

    assert credentials.token() == "ghs_minted"

    (url,) = post.call_args.args
    assert url == "https://api.github.com/app/installations/5678/access_tokens"
    claimed = claims(post.call_args.kwargs["headers"], public)
    assert claimed["iss"] == "1234"
    assert claimed["exp"] - claimed["iat"] == 660
    assert credentials.expires_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_app_installation_holds_the_token_it_minted(signing_key: tuple[str, str]) -> None:
    """Exchange once, not once per request."""
    private, _ = signing_key
    credentials, post = installation(private, exchange({"token": "ghs_minted", "expires_at": EXPIRES_AT}))

    assert credentials.token() == credentials.token() == "ghs_minted"
    assert post.call_count == 1


def test_app_installation_refreshes_unconditionally(signing_key: tuple[str, str]) -> None:
    """Replace a held token on demand, and report that a genuinely new one arrived."""
    private, _ = signing_key
    credentials, post = installation(
        private,
        exchange({"token": "first", "expires_at": EXPIRES_AT}),
        exchange({"token": "second", "expires_at": EXPIRES_AT}),
    )

    assert credentials.token() == "first"
    assert credentials.refresh() is True
    assert credentials.token() == "second"
    assert post.call_count == 2


def test_app_installation_reports_a_refused_exchange(signing_key: tuple[str, str]) -> None:
    """Carry GitHub's own account of a refusal, on one line."""
    private, _ = signing_key
    body = json.dumps({"message": "Integration not found"})
    credentials, _post = installation(private, exchange(None, status_code=404, text=body))

    with pytest.raises(CredentialsError, match="Integration not found"):
        credentials.token()


def test_app_installation_reports_a_transport_failure(signing_key: tuple[str, str]) -> None:
    """Fail the run when the exchange cannot be made at all, after the attempts are spent."""
    private, _ = signing_key
    waits: list[float] = []
    failure = RequestsConnectionError("name resolution failed")
    credentials, post = installation(private, failure, failure, failure, waits=waits)

    with pytest.raises(CredentialsError, match="after 3 attempts: name resolution failed"):
        credentials.token()

    assert post.call_count == 3
    assert waits == [1, 2]


def test_app_installation_retries_a_transport_failure(signing_key: tuple[str, str]) -> None:
    """Keep a run alive through one dropped connection, rather than failing before it collects."""
    private, _ = signing_key
    waits: list[float] = []
    credentials, post = installation(
        private,
        RequestsConnectionError("connection reset"),
        exchange({"token": "ghs_minted", "expires_at": EXPIRES_AT}),
        waits=waits,
    )

    assert credentials.token() == "ghs_minted"
    assert post.call_count == 2
    assert waits == [1]


def test_app_installation_retries_a_transient_refusal(signing_key: tuple[str, str]) -> None:
    """Treat a 502 from the exchange as GitHub having a bad second, not as a bad key."""
    private, _ = signing_key
    waits: list[float] = []
    credentials, post = installation(
        private,
        exchange(None, status_code=502, text="<html>Bad gateway</html>"),
        exchange({"token": "ghs_minted", "expires_at": EXPIRES_AT}),
        waits=waits,
    )

    assert credentials.token() == "ghs_minted"
    assert post.call_count == 2
    assert waits == [1]


def test_app_installation_stops_retrying_a_transient_refusal(signing_key: tuple[str, str]) -> None:
    """Give up on a bounded number of attempts rather than holding a run open indefinitely."""
    private, _ = signing_key
    unavailable = exchange({"message": "Service unavailable"}, status_code=503)
    credentials, post = installation(private, unavailable, unavailable, unavailable)

    with pytest.raises(CredentialsError, match="HTTP 503: Service unavailable"):
        credentials.token()

    assert post.call_count == 3


def test_app_installation_does_not_retry_a_refused_key(signing_key: tuple[str, str]) -> None:
    """Report a rejected JWT once, because the second identical request gets the identical answer."""
    private, _ = signing_key
    credentials, post = installation(private, exchange({"message": "A JSON web token could not be decoded"}, 401))

    with pytest.raises(CredentialsError, match="could not be decoded"):
        credentials.token()

    assert post.call_count == 1


@pytest.mark.parametrize("json_fails", [True, False])
def test_app_installation_reports_a_refusal_without_a_message(
    signing_key: tuple[str, str], *, json_fails: bool
) -> None:
    """Fall back to the body, on one line, when a refusal carries no `message` for a human to read."""
    private, _ = signing_key
    page = "<html>\n  <body>Not Found</body>\n</html>"
    answer = exchange({"documentation_url": "https://docs.github.com"}, status_code=404, text=page)
    if json_fails:
        answer.json.side_effect = ValueError("Expecting value")
    credentials, _post = installation(private, answer)

    with pytest.raises(CredentialsError, match=r"HTTP 404: <html> <body>Not Found</body> </html>"):
        credentials.token()


def test_app_installation_remints_inside_the_renewal_margin(signing_key: tuple[str, str]) -> None:
    """Replace a token before it expires, so no request is sent with one that dies in flight."""
    private, _ = signing_key
    credentials, post = installation(
        private,
        exchange({"token": "first", "expires_at": EXPIRES_AT}),
        exchange({"token": "second", "expires_at": "2026-09-01T13:00:00Z"}),
        now=datetime(2026, 9, 1, 11, 56, tzinfo=UTC),
    )

    assert credentials.token() == "first"
    assert credentials.token() == "second"
    assert post.call_count == 2


def test_app_installation_keeps_the_held_token_when_an_early_renewal_fails(
    signing_key: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Carry on with a token that still works, rather than ending a long run five minutes early.

    The whole point of renewing inside a margin: the credential still holds a usable token when the
    exchange is attempted, so a bad second at GitHub must not cost a collection its inventory and its
    alert observations. Later requests attempt the exchange again.
    """
    private, _ = signing_key
    unavailable = exchange({"message": "Service unavailable"}, status_code=503)
    credentials, post = installation(
        private,
        exchange({"token": "first", "expires_at": EXPIRES_AT}),
        unavailable,
        unavailable,
        unavailable,
        now=datetime(2026, 9, 1, 11, 56, tzinfo=UTC),
    )

    assert credentials.token() == "first"
    with caplog.at_level(logging.WARNING):
        assert credentials.token() == "first"

    assert post.call_count == 4
    assert "continuing with the held token" in caplog.text


def test_app_installation_fails_when_a_renewal_fails_past_the_expiry(signing_key: tuple[str, str]) -> None:
    """Fail the run once the held token is genuinely spent, because there is nothing left to send."""
    private, _ = signing_key
    refused = exchange({"message": "Service unavailable"}, status_code=503)
    credentials, _post = installation(
        private,
        exchange({"token": "first", "expires_at": EXPIRES_AT}),
        refused,
        refused,
        refused,
        now=datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
    )

    assert credentials.token() == "first"
    with pytest.raises(CredentialsError, match="HTTP 503: Service unavailable"):
        credentials.token()


def test_app_installation_holds_a_token_outside_the_renewal_margin(signing_key: tuple[str, str]) -> None:
    """Keep a token with more than the margin left, rather than minting one per request."""
    private, _ = signing_key
    credentials, post = installation(
        private,
        exchange({"token": "first", "expires_at": EXPIRES_AT}),
        now=datetime(2026, 9, 1, 11, 54, tzinfo=UTC),
    )

    assert credentials.token() == credentials.token() == "first"
    assert post.call_count == 1


def test_app_installation_keeps_a_token_whose_expiry_is_unknown(signing_key: tuple[str, str]) -> None:
    """Hold a token GitHub gave no readable expiry for, instead of re-minting on every read."""
    private, _ = signing_key
    credentials, post = installation(private, exchange({"token": "ghs_minted"}))

    assert credentials.token() == credentials.token() == "ghs_minted"
    assert credentials.expires_at is None
    assert post.call_count == 1


def test_app_installation_signs_a_jwt_from_the_injected_clock(signing_key: tuple[str, str]) -> None:
    """Backdate `iat` from the clock this credential was given, not from the machine's own."""
    private, public = signing_key
    credentials, post = installation(private, exchange({"token": "ghs", "expires_at": EXPIRES_AT}))
    credentials.token()

    claimed = claims(post.call_args.kwargs["headers"], public)
    assert claimed["iat"] == int(datetime(2026, 9, 1, 10, 59, tzinfo=UTC).timestamp())
    assert claimed["exp"] == int(datetime(2026, 9, 1, 11, 10, tzinfo=UTC).timestamp())


def test_app_installation_reports_an_unreadable_body(signing_key: tuple[str, str]) -> None:
    """Treat a success that is not JSON as a failure to authenticate, not as a token."""
    private, _ = signing_key
    answer = exchange(None)
    answer.json.side_effect = ValueError("Expecting value")
    credentials, _post = installation(private, answer)

    with pytest.raises(CredentialsError, match="unreadable body"):
        credentials.token()


@pytest.mark.parametrize("payload", [{"expires_at": EXPIRES_AT}, {"token": ""}, "not-a-mapping"])
def test_app_installation_reports_a_body_without_a_token(signing_key: tuple[str, str], payload: object) -> None:
    """Never return an empty or absent token as though it were one."""
    private, _ = signing_key
    credentials, _post = installation(private, exchange(payload))

    with pytest.raises(CredentialsError, match="no installation token"):
        credentials.token()


@pytest.mark.parametrize(("expiry", "warns"), [(None, False), ("one hour from now", True)])
def test_app_installation_treats_an_unreadable_expiry_as_unknown(
    signing_key: tuple[str, str],
    expiry: object,
    caplog: pytest.LogCaptureFixture,
    *,
    warns: bool,
) -> None:
    """Keep a working token whose expiry cannot be read, and fall back to the 401 retry for it.

    An expiry GitHub sent in a form nobody can parse is warned about and an absent one is not: the
    first is GitHub doing something unexpected that a human should see, the second is a field a
    caller simply has to manage without.
    """
    private, _ = signing_key
    credentials, _post = installation(private, exchange({"token": "ghs_minted", "expires_at": expiry}))

    with caplog.at_level(logging.WARNING):
        assert credentials.token() == "ghs_minted"

    assert credentials.expires_at is None
    reported = "GitHub reported an unreadable installation token expiry: one hour from now"
    assert (reported in caplog.text) is warns


def test_app_installation_reads_a_naive_expiry_as_utc(signing_key: tuple[str, str]) -> None:
    """Assume UTC for an expiry GitHub sends without a zone, since that is the only zone it uses."""
    private, _ = signing_key
    credentials, _post = installation(private, exchange({"token": "ghs", "expires_at": "2026-09-01T12:00:00"}))
    credentials.token()

    assert credentials.expires_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_app_installation_reports_an_unusable_private_key() -> None:
    """Say that the key could not sign, rather than sending an unsigned request to GitHub."""
    credentials, post = installation("not a pem")

    with pytest.raises(CredentialsError, match="could not be used to sign"):
        credentials.token()

    assert post.call_count == 0


def test_app_installation_reports_a_passphrase_protected_key() -> None:
    """Report an encrypted PEM as a bad key: PyJWT raises TypeError here, not a PyJWTError."""
    generated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encrypted = generated.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(b"passphrase"),
    ).decode()
    credentials, post = installation(encrypted)

    with pytest.raises(CredentialsError, match="could not be used to sign"):
        credentials.token()

    assert post.call_count == 0


def test_app_installation_reports_a_public_key_given_as_the_private_one(signing_key: tuple[str, str]) -> None:
    """Report the wrong `.pem`: a public key parses, then fails as AttributeError when asked to sign."""
    _private, public = signing_key
    credentials, post = installation(public)

    with pytest.raises(CredentialsError, match="could not be used to sign"):
        credentials.token()

    assert post.call_count == 0


def test_app_installation_defaults_its_clock_to_the_real_one() -> None:
    """Read the wall clock when nobody injected one, since a real run has no fixture instant."""
    credentials = AppInstallation(1234, 5678, "key", MagicMock())

    assert credentials.clock().tzinfo is UTC


def test_redacted_blanks_a_key_quoted_one_line_at_a_time() -> None:
    """Hide a single base64 line of a PEM, because quoting one line still publishes part of a key."""
    key = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQ\n-----END PRIVATE KEY-----"

    assert redacted("could not parse MIIEvgIBADANBgkqhkiG9w0BAQ here", key) == "could not parse [redacted] here"


def test_redacted_replaces_a_whole_key_with_one_marker() -> None:
    """Blank a key quoted in full as one thing, rather than as a marker for each of its lines."""
    key = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQ\n-----END PRIVATE KEY-----"

    assert redacted(f"rejected {key}", key) == "rejected [redacted]"


def test_redacted_leaves_a_short_fragment_alone() -> None:
    """Keep a message readable: blanking every short string would leave nothing to diagnose from."""
    assert redacted("the exchange ended", "ended") == "the exchange ended"


def test_a_successful_mint_logs_the_expiry_and_not_the_token(
    signing_key: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Say when the token stops working at DEBUG, which is all a diagnosis needs, and never what it is."""
    private, _ = signing_key
    minted = "ghs_averyrealtokenindeed"
    credentials, post = installation(private, exchange({"token": minted, "expires_at": EXPIRES_AT}))

    with caplog.at_level(logging.DEBUG):
        assert credentials.token() == minted

    sent = post.call_args.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
    assert minted not in caplog.text
    assert sent not in caplog.text
    assert private not in caplog.text
    assert "minted a GitHub App installation token expiring at 2026-09-01 12:00:00+00:00" in caplog.text


def test_a_failed_exchange_writes_neither_the_jwt_nor_the_key(
    signing_key: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep a verbose refusal from publishing the credential it refused.

    GitHub does not quote a rejected JWT back, but the thing answering may not be GitHub — a proxy or
    a gateway in the path writes its own error page, and a refusal's body is the one part of a
    response this module copies into a message a human reads. The exchange here answers with the
    assertion it was sent, on every attempt, so the retry warning and the terminal error are both
    read for it.
    """
    private, _ = signing_key
    sent: list[str] = []

    def refuse(_url: str, *, headers: Mapping[str, str], **_kwargs: object) -> MagicMock:
        sent.append(headers["Authorization"].removeprefix("Bearer "))
        return exchange({"message": f"rejected {headers['Authorization']}"}, status_code=503)

    session = MagicMock()
    session.post.side_effect = refuse
    credentials = AppInstallation(1234, 5678, private, session, clock=lambda: NOW, pause=lambda _seconds: None)

    with caplog.at_level(logging.DEBUG), pytest.raises(CredentialsError) as captured:
        credentials.token()

    assert len(sent) == 3
    for secret in (*sent, private, *private.splitlines()):
        assert secret not in caplog.text
        assert secret not in str(captured.value)
    assert "[redacted]" in caplog.text
    assert "[redacted]" in str(captured.value)


def test_a_transport_failure_that_quotes_the_request_is_redacted(signing_key: tuple[str, str]) -> None:
    """Blank the assertion out of a transport error that echoed the headers it failed to send."""
    private, _ = signing_key
    sent: list[str] = []

    def fail(_url: str, *, headers: Mapping[str, str], **_kwargs: object) -> MagicMock:
        sent.append(headers["Authorization"].removeprefix("Bearer "))
        quoted = f"failed to send {headers['Authorization']}"
        raise RequestsConnectionError(quoted)

    session = MagicMock()
    session.post.side_effect = fail
    waits: list[float] = []
    credentials = AppInstallation(1234, 5678, private, session, clock=lambda: NOW, pause=waits.append)

    with pytest.raises(CredentialsError) as captured:
        credentials.token()

    assert sent[0] not in str(captured.value)
    assert "after 3 attempts: failed to send Bearer [redacted]" in str(captured.value)
    assert waits == [1, 2]


def test_an_unusable_key_is_not_quoted_back_by_the_failure(signing_key: tuple[str, str]) -> None:
    """Redact a signing library that reports the key it could not read by printing it.

    PyJWT and `cryptography` do not do this today, but they are the one place in this module where
    the private key itself is passed to somebody else's parser, and an error message is the natural
    place for a parser to put its input.
    """
    private, _ = signing_key
    credentials, post = installation(private)

    quoted = ValueError(f"could not deserialize {private}")
    with (
        patch("metrics.credentials.jwt.encode", side_effect=quoted),
        pytest.raises(CredentialsError) as captured,
    ):
        credentials.token()

    assert "could not be used to sign a request: could not deserialize [redacted]" in str(captured.value)
    assert private not in str(captured.value)
    assert post.call_count == 0

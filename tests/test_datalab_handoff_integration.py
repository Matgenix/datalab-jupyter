"""Cross-repository regression coverage for the browser-bound Jupyter handoff."""

import asyncio
import copy
import os
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from datalab_jupyter.hub.authenticator import DatalabAuthenticator, _pkce_s256_challenge

pytestmark = pytest.mark.skipif(
    os.environ.get("DATALAB_CROSS_REPOSITORY_TEST") != "1",
    reason="requires the opt-in Mongo-backed cross-repository test environment",
)

_CLIENT_ID = "datalab-jupyter"
_CLIENT_SECRET = "integration-client-secret-value-123456"  # noqa: S105
_APP_SECRET = "integration-app-secret-value-123456789"  # noqa: S105
_USER_A_ID = "111111111111111111111111"
_USER_B_ID = "777777777777777777777777"
_VERIFIER_A = "A" * 43
_VERIFIER_B = "B" * 43
_LAUNCH_PATH = "/v0.1/tools/jupyter/launch"
_AUTHORIZE_PATH = "/v0.1/tools/jupyter/service-launch/authorize"


@pytest.fixture(scope="module")
def core_app(tmp_path_factory):
    """Create the real core app with the installed Jupyter provider and two users."""
    import pymongo
    from bson import ObjectId

    environment = pytest.MonkeyPatch()
    environment.setenv("DATALAB_JUPYTER_CLIENT_ID", _CLIENT_ID)
    environment.setenv("DATALAB_JUPYTER_CLIENT_SECRET", _CLIENT_SECRET)
    environment.setenv("DATALAB_JUPYTER_PUBLIC_URL", "http://localhost:8000/jupyter")
    environment.delenv("DATALAB_JUPYTER_EXTERNAL_URL", raising=False)

    database_name = f"__datalab_jupyter_integration_{uuid4().hex}"
    mongo_uri = f"mongodb://127.0.0.1:27017/{database_name}"
    mongo_client = pymongo.MongoClient(
        mongo_uri,
        connectTimeoutMS=1000,
        serverSelectionTimeoutMS=1000,
    )
    mongo_client.admin.command("ping")
    mongo_client.drop_database(database_name)

    files_directory = tmp_path_factory.mktemp("datalab-jupyter-integration") / "files"
    from pydatalab.main import create_app
    from pydatalab.tools.registry import TOOL_REGISTRY_EXTENSION

    app = create_app(
        {
            "APP_URL": "http://localhost",
            "FILE_DIRECTORY": str(files_directory),
            "MONGO_URI": mongo_uri,
            "ROOT_PATH": "/",
            "SECRET_KEY": _APP_SECRET,
            "TESTING": True,
            "TOOLS": {
                "ALLOW_INSECURE_HTTP": False,
                "DISABLED": set(),
                "ORDER": [],
            },
        },
        env_file=False,
    )
    assert app.extensions[TOOL_REGISTRY_EXTENSION].get("jupyter") is not None

    database = mongo_client.get_database(database_name)
    database.users.insert_many(
        [
            {
                "_id": ObjectId(_USER_A_ID),
                "contact_email": "user-a@example.org",
                "display_name": "User A",
                "account_status": "active",
            },
            {
                "_id": ObjectId(_USER_B_ID),
                "contact_email": "user-b@example.org",
                "display_name": "User B",
                "account_status": "active",
            },
        ]
    )
    database.roles.insert_many(
        [
            {"_id": ObjectId(_USER_A_ID), "role": "user"},
            {"_id": ObjectId(_USER_B_ID), "role": "user"},
        ]
    )

    try:
        yield app, database
    finally:
        from pydatalab.mongo import flask_mongo

        if flask_mongo.cx:
            flask_mongo.cx.close()
        mongo_client.drop_database(database_name)
        mongo_client.close()
        environment.undo()


@pytest.fixture
def browsers(core_app):
    """Return isolated Datalab browser sessions and a clean grant/session store."""
    app, database = core_app
    database.tool_launch_grants.delete_many({})
    database.tool_sessions.delete_many({})

    def browser(user_id=None):
        client = app.test_client()
        if user_id is not None:
            with client.session_transaction() as session:
                session["_user_id"] = user_id
        return client

    return SimpleNamespace(
        user_a=browser(_USER_A_ID),
        user_b=browser(_USER_B_ID),
        database=database,
    )


@pytest.fixture(scope="module")
def live_core_url(core_app):
    """Serve the real provider exchange on an ephemeral loopback port."""
    from werkzeug.serving import make_server

    app, _database = core_app
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def authenticator(live_core_url):
    return DatalabAuthenticator(
        api_url=live_core_url,
        browser_api_url=live_core_url,
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
    )


def _fragment_value(url, field):
    values = parse_qs(urlsplit(url).fragment, strict_parsing=True).get(field)
    assert values is not None and len(values) == 1
    return values[0]


def _launch(browser):
    response = browser.post(
        _LAUNCH_PATH,
        json={},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 200
    return _fragment_value(response.get_json()["url"], "datalab_launch_code")


def _authorize(browser, launch_code, verifier):
    return browser.post(
        _AUTHORIZE_PATH,
        data={
            "datalab_launch_code": launch_code,
            "code_challenge": _pkce_s256_challenge(verifier),
            "code_challenge_method": "S256",
        },
        headers={"Origin": "http://localhost"},
    )


def _authorization_code(response):
    assert response.status_code == 303
    return _fragment_value(response.headers["Location"], "datalab_authorization_code")


def _authenticate(authenticator, authorization_code, verifier, expected_user_id=None, **state):
    handler = SimpleNamespace(
        _datalab_authorization_code=authorization_code,
        _datalab_code_verifier=verifier,
        _datalab_expected_user_id=expected_user_id,
        **state,
    )
    return asyncio.run(authenticator.authenticate(handler)), handler


def test_user_a_completes_real_handoff_once(browsers, authenticator):
    launch_code = _launch(browsers.user_a)
    authorization_code = _authorization_code(_authorize(browsers.user_a, launch_code, _VERIFIER_A))

    authenticated, handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_A,
        expected_user_id=_USER_A_ID,
    )

    assert authenticated["name"] == f"datalab-{_USER_A_ID}"
    assert authenticated["auth_state"]["current_user"]["id"] == _USER_A_ID
    assert handler._datalab_exchange.user_id == _USER_A_ID
    assert browsers.database.tool_launch_grants.count_documents({}) == 0
    assert browsers.database.tool_sessions.count_documents({}) == 1

    replay, replay_handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_A,
        expected_user_id=_USER_A_ID,
    )
    assert replay is None
    assert not hasattr(replay_handler, "_datalab_exchange")
    assert browsers.database.tool_sessions.count_documents({}) == 1


def test_copied_initial_launch_is_not_consumed_by_user_b(browsers, authenticator):
    launch_code = _launch(browsers.user_a)

    rejected = _authorize(browsers.user_b, launch_code, _VERIFIER_B)

    assert rejected.status_code == 400
    assert b"invalid or has expired" in rejected.data
    assert browsers.database.tool_launch_grants.count_documents({}) == 1
    assert browsers.database.tool_sessions.count_documents({}) == 0

    authorization_code = _authorization_code(_authorize(browsers.user_a, launch_code, _VERIFIER_A))
    authenticated, _handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_A,
        expected_user_id=_USER_A_ID,
    )
    assert authenticated["auth_state"]["current_user"]["id"] == _USER_A_ID


def test_copied_callback_fails_in_a_fresh_hub_context(browsers, authenticator):
    launch_code = _launch(browsers.user_a)
    authorization_code = _authorization_code(_authorize(browsers.user_a, launch_code, _VERIFIER_A))

    rejected, victim_handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_B,
    )

    assert rejected is None
    assert not hasattr(victim_handler, "_datalab_exchange")
    assert browsers.database.tool_launch_grants.count_documents({}) == 1
    assert browsers.database.tool_sessions.count_documents({}) == 0

    authenticated, _handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_A,
        expected_user_id=_USER_A_ID,
    )
    assert authenticated["auth_state"]["current_user"]["id"] == _USER_A_ID


def test_existing_user_b_cannot_switch_to_user_a(browsers, authenticator):
    launch_code = _launch(browsers.user_a)
    authorization_code = _authorization_code(_authorize(browsers.user_a, launch_code, _VERIFIER_A))
    victim_state = {
        "hub_cookie_subject": _USER_B_ID,
        "auth_state": {"current_user": {"id": _USER_B_ID}},
        "server_identity": f"datalab-{_USER_B_ID}",
        "volume_name": f"datalab-jupyter-datalab-{_USER_B_ID}",
    }
    expected_state = copy.deepcopy(victim_state)

    rejected, victim_handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_A,
        expected_user_id=_USER_B_ID,
        **victim_state,
    )

    assert rejected is None
    assert not hasattr(victim_handler, "_datalab_exchange")
    assert {name: getattr(victim_handler, name) for name in expected_state} == expected_state
    assert browsers.database.tool_launch_grants.count_documents({}) == 1
    assert browsers.database.tool_sessions.count_documents({}) == 0

    authenticated, _handler = _authenticate(
        authenticator,
        authorization_code,
        _VERIFIER_A,
        expected_user_id=_USER_A_ID,
    )
    assert authenticated["auth_state"]["current_user"]["id"] == _USER_A_ID

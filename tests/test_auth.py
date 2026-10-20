from services import auth


def test_auth_request_uses_supabase_bearer_token(monkeypatch):
    captured = {}

    class Response:
        ok = True

        @staticmethod
        def json():
            return {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("SUPABASE_URL", "https://planner.supabase.co")
    monkeypatch.setattr(auth.requests, "post", fake_post)

    auth._request("logout", {}, api_key="public-key", access_token="session-token")

    assert captured["url"] == "https://planner.supabase.co/auth/v1/logout"
    assert captured["headers"]["Authorization"] == "Bearer session-token"
    assert captured["headers"]["apikey"] == "public-key"

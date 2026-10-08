"""Tests for the network layer, using a fake requests session (no live calls)."""


from histwardb import HistWarDB


class FakeResponse:
    def __init__(self, bindings, status_code=200):
        self._bindings = bindings
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"results": {"bindings": self._bindings}}


class FakeSession:
    """Returns the same canned bindings for every chunk request."""

    def __init__(self, bindings):
        self.bindings = bindings
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self.bindings)


class FailingSession:
    def get(self, url, params=None, headers=None, timeout=None):
        import requests

        raise requests.ConnectionError("simulated network failure")


def test_fetch_data_uses_injected_session(sample_bindings):
    session = FakeSession(sample_bindings)
    db = HistWarDB(start_year=1925, end_year=1975, chunk_size=25, session=session)
    assert db.df_long is not None
    assert not db.df_long.empty
    # one request per 25-year chunk in [1925, 1975)
    assert len(session.calls) == 2


def test_fetch_data_survives_network_failures():
    db = HistWarDB(start_year=1900, end_year=1950, session=FailingSession())
    # all chunks fail -> empty but initialized dataframe, no exception raised
    assert db.df_long is not None
    assert db.df_long.empty

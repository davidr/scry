"""Tests for WINDOW_HISTORY update logic."""

from scry.scry import update_window_history


def test_add_to_empty(clean_window_history):
    """First window appended."""
    update_window_history("@1")
    assert list(clean_window_history) == ["@1"]


def test_append_different(clean_window_history):
    """New window appended to end."""
    update_window_history("@1")
    update_window_history("@2")
    assert list(clean_window_history) == ["@1", "@2"]


def test_no_duplicate_last(clean_window_history):
    """Re-adding last window is a no-op."""
    update_window_history("@1")
    update_window_history("@2")
    update_window_history("@2")
    assert list(clean_window_history) == ["@1", "@2"]


def test_moves_existing_to_end(clean_window_history):
    """Existing window removed from middle, appended to end."""
    update_window_history("@1")
    update_window_history("@2")
    update_window_history("@3")
    update_window_history("@1")
    assert list(clean_window_history) == ["@2", "@3", "@1"]


def test_history_cleanup_remote_present(remote_sections, tmp_path):
    """A remote entry is present while a section row matches it."""
    from scry.remote.cache import RemoteTabRef
    from scry.scry import history_entry_present

    _, _, sections = remote_sections(tmp_path)
    assert history_entry_present(RemoteTabRef("b.example", "1:2", "9"), [], sections)


def test_history_cleanup_remote_absent_after_identity_change(remote_sections, tmp_path):
    """Same host and tab id with a different identity is absent."""
    from scry.remote.cache import RemoteTabRef
    from scry.scry import history_entry_present

    _, _, sections = remote_sections(tmp_path)
    assert not history_entry_present(RemoteTabRef("b.example", "9:9", "9"), [], sections)


def test_history_cleanup_local(mock_windows):
    """Local entries match local windows only."""
    from scry.scry import history_entry_present

    assert history_entry_present("@2", mock_windows, [])
    assert not history_entry_present("@99", mock_windows, [])


def test_local_and_remote_ids_dont_collide(clean_window_history):
    """A local zellij id '5' and a remote tab '5' are distinct history entries."""
    from scry.remote.cache import RemoteTabRef

    ref = RemoteTabRef("h", "1:2", "5")
    update_window_history("5")
    update_window_history(ref)
    assert list(clean_window_history) == ["5", ref]

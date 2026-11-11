# Copyright (c) 2025-2026 Datalayer, Inc.
# Distributed under the terms of the Modified BSD License.

"""A team's seat session is opened with a ULID, as every session the platform opens."""

import re

from agent_teams.sessions.store import new_uid

ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def test_a_new_session_uid_is_a_ulid():
    uids = [new_uid() for _ in range(50)]
    assert all(ULID.fullmatch(uid) for uid in uids)
    assert len(set(uids)) == 50


def test_ulids_sort_by_when_they_were_minted():
    import time

    first = new_uid()
    time.sleep(0.002)
    assert new_uid()[:10] >= first[:10]

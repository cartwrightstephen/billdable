"""Jobs that bill in dollars, for tests of dollar billing through the HTTP API.

Every job made through New job now starts with a schedule of values (Nov 3 Decision 1); jobs made
before that have none and keep dollar progress. A test class decorated with @dollar_jobs gets those
older jobs from POST /api/jobs.
"""

from unittest import mock

import domain

_create_job = domain.create_job


def _older_job(*args, **kwargs):
    kwargs["auto_schedule"] = False
    return _create_job(*args, **kwargs)


def dollar_jobs(cls):
    inherited = cls.setUpClass.__func__

    def setUpClass(c):
        patcher = mock.patch.object(domain, "create_job", _older_job)
        patcher.start()
        c.addClassCleanup(patcher.stop)
        inherited(c)

    cls.setUpClass = classmethod(setUpClass)
    return cls

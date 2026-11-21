"""The demo company as the Hartline Remodeling fixture (seed.py) for a test class.

The live demo is Redbud Electric (demo_seed.py, Stephen, 10 Nov, decision 11). Test classes that
make the demo company through server.ensure_demo, or reset it, were written against Hartline's
jobs and figures; @hartline_demo keeps them on it (DURACASH_DEMO_SEED=hartline, as the browser
suite runs), and keeps the electrical demo's files out of the real uploads folder.
"""

from unittest import mock


def hartline_demo(cls):
    inherited = cls.setUpClass.__func__

    def setUpClass(c):
        patcher = mock.patch.dict("os.environ", {"DURACASH_DEMO_SEED": "hartline"})
        patcher.start()
        c.addClassCleanup(patcher.stop)
        inherited(c)

    cls.setUpClass = classmethod(setUpClass)
    return cls

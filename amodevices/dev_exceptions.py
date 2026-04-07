# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/Berkeley

Definitions of device exceptions.
"""


class DeviceError(Exception):
    """Device error.

    The single positional argument is the human-readable error message
    (a string).  Wrap upstream exceptions with ``from`` so the original
    traceback survives via ``__cause__``::

        try:
            ...
        except SomeError as exc:
            raise DeviceError(f'Failed to do X: {exc}') from exc

    Or, when there is no extra context to add::

        except SomeError as exc:
            raise DeviceError(str(exc)) from exc

    The constructor coerces its argument through ``str()`` defensively,
    so legacy callers that pass an exception object directly still
    produce a JSON-serialisable ``args[0]``.  This matters for
    consumers like pydase that serialise exceptions across an RPC
    boundary by reading ``obj.args[0]`` and JSON-encoding it — a raw
    exception instance there would crash the encoder and break the
    connection.
    """

    def __init__(self, message: str) -> None:
        super().__init__(str(message))

    @property
    def value(self) -> str:
        """Backward-compat alias for ``str(self)``.

        New code should prefer ``str(exc)`` (or ``exc.args[0]``),
        which works on every exception type and matches stdlib
        convention.  This shim keeps existing ``e.value`` readers
        working unchanged.
        """
        return self.args[0] if self.args else ''

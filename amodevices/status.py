# -*- coding: utf-8 -*-
"""Fleet-wide reading STATUS vocabulary.

A driver that can tell a valid reading from an invalid one reports a
plain-word status STRING beside each reading — written to the database
as a companion field and shown to people verbatim, so it has to be
readable without a code table. Convention, shared by every driver and
every consumer (loggers, GUIs):

- snake_case, ``[a-z0-9_]`` only, at most 32 characters
  (`STATUS_PATTERN`);
- `STATUS_OK` ('ok') is the universal "valid reading" token;
- otherwise a short plain-English reason, the SAME word across devices
  where the meaning matches — a saturating detector is 'overexposed'
  whatever the instrument, a dark one 'no_signal';
- a device code with no mapping becomes `STATUS_UNKNOWN`
  ('unknown_error') in the data; the raw code belongs in a log line,
  never in the record;
- a status is a string fleet-wide (a database fixes a field's type at
  its first write) and never contains a comma (CSV consumers).

Each driver keeps its own code → word table next to its codes (e.g.
`HighFinesseWS.STATUS_TEXT`) and maps with `status_for`; a test can
check the table against the convention with `check_status_table`.
Transport-free on purpose: importable without any device library.
"""

import re

STATUS_OK = 'ok'
STATUS_UNKNOWN = 'unknown_error'
STATUS_PATTERN = re.compile(r'^[a-z0-9_]{1,32}$')


def check_status_word(word):
    """Return `word` if it follows the convention; raise `ValueError`
    otherwise."""
    if not isinstance(word, str) or not STATUS_PATTERN.match(word):
        raise ValueError(
            f'Status word {word!r} violates the convention (snake_case,'
            ' [a-z0-9_], at most 32 characters)')
    return word


def status_for(code, table):
    """The status word of a device `code` in the driver's `table`;
    `STATUS_UNKNOWN` for a code without a mapping. Accepts the float a
    C API may return for an integer code."""
    return table.get(int(code), STATUS_UNKNOWN)


def check_status_table(table):
    """Validate a driver's code → word table: every word follows the
    convention, no two codes share a word, and no code maps to the
    reserved 'ok' or 'unknown_error'. Raises `ValueError`."""
    words = list(table.values())
    for word in words:
        check_status_word(word)
        if word in (STATUS_OK, STATUS_UNKNOWN):
            raise ValueError(f'Status table maps a code to the reserved word {word!r}')
    if len(set(words)) != len(words):
        raise ValueError('Status table maps two codes to the same word')
    return table

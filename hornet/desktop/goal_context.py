"""Thread-local provenance for reconciling an interrupted revision publication."""
import threading
import copy
from contextlib import contextmanager

_local = threading.local()


@contextmanager
def publication_tag(operation_id, checkpoint, input_fingerprint, snapshot=None):
    previous = getattr(_local, 'tag', None)
    previous_snapshot = getattr(_local, 'snapshot', None)
    _local.snapshot = copy.deepcopy(snapshot)
    _local.tag = {'operation_id': operation_id, 'checkpoint': checkpoint,
                  'input_fingerprint': input_fingerprint}
    try:
        yield
    finally:
        _local.tag = previous
        _local.snapshot = previous_snapshot


def current_tag():
    value = getattr(_local, 'tag', None)
    return dict(value) if value else None


def current_snapshot():
    return copy.deepcopy(getattr(_local, 'snapshot', None))

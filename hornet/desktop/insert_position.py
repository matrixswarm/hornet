"""Resolve explicit insert intent against the current file, never guessed offsets."""

def resolve_insert(args, text=None):
    result = dict(args)
    if result.get('action') != 'insert':
        if 'position' in result or 'anchor_text' in result:
            raise ValueError('position and anchor_text are only valid for insert.')
        return result
    if 'position' not in result:
        if 'anchor_text' in result:
            raise ValueError('anchor_text requires before or after position.')
        if 'line_number' not in result:
            raise ValueError('insert requires position or line_number.')
        return result
    if 'line_number' in result:
        raise ValueError('insert must use position OR line_number, never both.')
    position = result.pop('position')
    if position not in ('start', 'end', 'before', 'after'):
        raise ValueError('insert position must be start, end, before, or after.')
    anchor = result.pop('anchor_text', None)
    if position in ('before', 'after'):
        if not isinstance(anchor, str) or not anchor or not anchor.splitlines():
            raise ValueError('before/after requires nonempty exact whole-line anchor_text.')
    elif anchor is not None:
        raise ValueError('start/end must not include anchor_text.')
    # Shape validation precedes sequential rehearsal; no position is executed here.
    if text is None:
        result['line_number'] = 1
        return result
    lines = text.splitlines()
    if position == 'start':
        number = 1
    elif position == 'end':
        number = len(lines) + 1
    else:
        block = anchor.splitlines()
        matches = [i for i in range(len(lines) - len(block) + 1)
                   if lines[i:i + len(block)] == block]
        if len(matches) != 1:
            raise ValueError('Expected one exact whole-line anchor block; found ' + str(len(matches)))
        number = matches[0] + 1 + (len(block) if position == 'after' else 0)
    result['line_number'] = number
    return result

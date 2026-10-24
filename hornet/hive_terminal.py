"""A dependency-free terminal observer for Windows and POSIX controllers."""
from contextlib import contextmanager
import os
from queue import Empty, Full, Queue
import select
import shutil
import sys
import textwrap
import threading
import time
import unicodedata

from . import hive_status
from .project import HornetError


def clean(value):
    # Checkpoints contain model-authored text: never render terminal commands.
    return ''.join(c if not unicodedata.category(c).startswith('C') else ' ' for c in str(value))


def age(action):
    seconds = hive_status.elapsed(action.get('observed_at')) if action else None
    if seconds is None:
        return 'unrecorded'
    if seconds < 60:
        return '{}s ago'.format(seconds)
    if seconds < 3600:
        return '{}m {}s ago'.format(seconds // 60, seconds % 60)
    if seconds < 86400:
        return '{}h {}m ago'.format(seconds // 3600, seconds % 3600 // 60)
    return '{}d {}h ago'.format(seconds // 86400, seconds % 86400 // 3600)


def inverted_tree(data, selected):
    nodes = [c for c in data['cells'] if c['goal_id'] == selected['goal_id'] and c['run_dir'] == selected['run_dir'] and c['depth'] > 0]
    lines = []
    def visit(node):
        for child in nodes:
            if child['parent'] == node['id']:
                visit(child)
        lines.append('{}{} {} [{}]'.format('  ' * node['depth'], '>' if node['id'] == selected['id'] else '-', node['id'], node['status']))
    for node in nodes:
        if node['parent'] is None:
            visit(node)
    lines.append(('> ' if selected['depth'] == 0 else '') + 'GOAL ' + selected['goal_id'])
    return lines


class View:
    def __init__(self):
        self.screen, self.index, self.goal_filter, self.selected = 'menu', 0, None, None
        self.notice = ''
        self.scroll = 0

    def rows(self, data):
        if self.screen == 'goals':
            return data['goals']
        return [c for c in data['cells'] if (self.screen != 'active' or c['active'])
                and (self.goal_filter is None or c['run_dir'] == self.goal_filter)]

    def key(self, key, data):
        if key in {'q', 'ctrl_c'}:
            return False
        if key in {'b', 'escape', 'backspace'}:
            self.screen, self.index, self.goal_filter, self.selected = 'menu', 0, None, None
            self.scroll = 0
        elif key in {'1', 'c', '2', 'a', '3', 'g'}:
            self.screen = {'1': 'cells', 'c': 'cells', '2': 'active', 'a': 'active', '3': 'goals', 'g': 'goals'}[key]
            self.index, self.goal_filter, self.selected = 0, None, None
            self.scroll = 0
        elif self.screen == 'detail':
            if key in {'down', 'j'}:
                self.scroll += 1
            elif key in {'up', 'k'}:
                self.scroll = max(0, self.scroll - 1)
        elif self.screen in {'cells', 'active', 'goals'}:
            rows = self.rows(data)
            self.index = min(self.index, max(0, len(rows) - 1))
            if key in {'down', 'j'}:
                self.index = min(self.index + 1, max(0, len(rows) - 1))
            elif key in {'up', 'k'}:
                self.index = max(0, self.index - 1)
            elif key == 'enter' and rows:
                row = rows[self.index]
                if self.screen == 'goals':
                    self.goal_filter, self.screen, self.index = row['run_dir'], 'cells', 0
                else:
                    self.selected = (row['run_dir'], row['id'])
                    self.screen = 'detail'
                    self.scroll = 0
        return True

    def lines(self, data, width=100, height=30):
        width, height = max(20, width), max(8, height)
        lines = ['HORNET | HIVE COMMAND CENTER',
                 '{} goals | {} cells | {} active | {}'.format(len(data['goals']), len(data['cells']), data['active_cells'], self.screen.upper())]
        footer = '1 Cells  2 Active  3 Goals  Enter Select  Up/Down Move  B Back  Q Quit'
        if self.screen == 'menu':
            lines += ['', '1  All cells', '2  Active cells being worked on', '3  Goals -> cells -> selected cell', '',
                      'Read-only live observations. No model requests or job changes.',
                      'Activity age uses recorded agent actions; unrecorded means unknown.',
                      'Completion counts are observed checks, not estimated percentages.',
                      '', 'Project: ' + data['root']]
        elif self.screen == 'detail':
            row = next((c for c in data['cells'] if (c['run_dir'], c['id']) == self.selected), None)
            if row is None:
                lines += ['', 'Selected cell is temporarily unavailable; refreshing.']
            else:
                goal = next(g for g in data['goals'] if g['run_dir'] == row['run_dir'])
                check = row['checks']
                action = row['last_action']
                lines += ['', 'Cell: ' + row['id'], 'Goal ID: ' + row['goal_id'], 'Run: ' + row['run'],
                          'State: {} | phase: {}'.format(row['status'], row['phase']),
                          'Assigned: {} | {}'.format(row['assigned_agent'] or '(direct model)', row['assigned_model']),
                          'Last used: {} | {}'.format(row['actual_agent'] or '(unrecorded)', row['actual_model'] or '(unrecorded)'),
                          'Last agent action: {}{} | {}'.format(action['event'] if action else 'unrecorded',
                              ' / ' + action['tool'] if action and action.get('tool') else '', age(action)),
                          'Checks: {} | Cell tools: {} | attempts: {}'.format(
                              '{}/{} passed'.format(check['passed'], check['total']) if check['observed'] else 'not yet observed ({} declared)'.format(check['total']), row['tools_used'], row['attempts']),
                          'Goal progress: {}/{} cells verified | requests {}/{} | tools {}/{}'.format(
                              goal['cells_verified'], goal['cells_total'], goal['model_requests'], goal['model_limit'], goal['tools_used'], goal['tool_limit'])]
                lines += textwrap.wrap('Goal: ' + clean(goal['goal']), width)[:3]
                if goal.get('supervisor'):
                    watch = goal['supervisor']
                    lines += ['Supervisor: ' + (watch['error'] if 'error' in watch else
                        '{} | activations {}/{}{}'.format('enabled' if watch['enabled'] else 'disabled',
                            watch['activations'], watch['limit'], ' | unresolved receipt' if watch['unresolved'] else ''))]
                if goal.get('revision'):
                    origin = goal['revision']
                    lines += ['Revision of {} in {}'.format(origin['source_cell_id'] or '(file selection)',
                                                          origin['parent_run'])]
                lines += textwrap.wrap('Task: ' + clean(row['task']), width)[:2]
                lines += ['Files: ' + (', '.join(row['files']) or '(orchestration)'),
                          'Depends on: ' + (', '.join(row['depends_on']) or '(none)')]
                if row['error']:
                    lines += ['Recorded error: ' + str(row['error'])]
                if (row['current'] and not row['verified'] and row['status'] in
                        {'interrupted', 'stopped', 'failed', 'stopped_by_operator', 'waiting_for_agent'}):
                    lines += ['', 'RESUME SELECTED CELL',
                              'hornet hive resume-cell "{}" {}'.format(row['run_dir'], row['id']),
                              'The controller checks the saved boundary; uncertain writes pause for inspection.']
                if row['verified'] and row['kind'] == 'code' and goal['status'] == 'completed':
                    lines += ['', 'COPY AS REVISION GOAL',
                              'hornet revise "{}" --cell {} --goal "DESCRIBE THE CHANGE"'.format(row['run_dir'], row['id']),
                              'The prior goal and cell remain intact; inherited checks run again.']
                lines += ['', 'INVERTED TREE (children above parents; > selected)'] + inverted_tree(data, row)
        else:
            rows = self.rows(data)
            self.index = min(self.index, max(0, len(rows) - 1))
            page = max(1, height - 8)
            start = self.index // page * page
            lines += ['', 'Showing {}-{} of {}'.format(start + 1 if rows else 0, min(start + page, len(rows)), len(rows))]
            for n, row in enumerate(rows[start:start + page], start):
                marker = '>' if n == self.index else ' '
                if self.screen == 'goals':
                    lines.append('{} {} [{}] {}/{} verified'.format(marker, row['id'], row['status'], row['cells_verified'], row['cells_total']))
                else:
                    lines.append('{} {:22} {:18} {:12} {}  goal:{}'.format(marker, clean(row['id'])[:22],
                        clean(row['status'])[:18], clean(row['assigned_agent'] or '(direct)')[:12], age(row['last_action']), row['goal_id'][-8:]))
            if not rows:
                lines += ['No active cells.' if self.screen == 'active' else 'No saved runs found. Select --run or --run-dir if your jobs are elsewhere.']
        if data['warnings']:
            lines += ['Warning: ' + data['warnings'][0]]
        if self.screen == 'detail':
            lines = [part for line in lines for part in (textwrap.wrap(clean(line), width,
                replace_whitespace=False, drop_whitespace=False) or [''])]
            self.scroll = min(self.scroll, max(0, len(lines) - (height - 2)))
            lines = lines[:2] + lines[2 + self.scroll:]
            footer = 'Up/Down or J/K Scroll  1 Cells  2 Active  B Back  Q Quit'
        clipped = [clean(line)[:width] for line in lines[:height - 2]]
        return clipped + [''] * max(0, height - 2 - len(clipped)) + [clean(footer)[:width]]


def read_key(timeout):
    if os.name == 'nt':
        import msvcrt
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in {'\x00', '\xe0'}:
                    return {'H': 'up', 'P': 'down'}.get(msvcrt.getwch(), '')
                return {'\r': 'enter', '\x1b': 'escape', '\x08': 'backspace', '\x03': 'ctrl_c'}.get(char, char.lower())
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        return ''
    if not select.select([sys.stdin], [], [], timeout)[0]:
        return ''
    char = os.read(sys.stdin.fileno(), 1).decode('ascii', errors='ignore')
    if char == '\x1b':
        rest = ''
        while len(rest) < 2 and select.select([sys.stdin], [], [], 0.03)[0]:
            rest += os.read(sys.stdin.fileno(), 1).decode('ascii', errors='ignore')
        return {'[A': 'up', '[B': 'down'}.get(rest, 'escape')
    return {'\r': 'enter', '\n': 'enter', '\x7f': 'backspace', '\x03': 'ctrl_c', '': 'q'}.get(char, char.lower())


@contextmanager
def screen():
    previous = None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetStdHandle.argtypes, kernel.GetStdHandle.restype = [wintypes.DWORD], wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle, mode = kernel.GetStdHandle(-11 & 0xffffffff), wintypes.DWORD()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)) or not kernel.SetConsoleMode(handle, mode.value | 4):
            raise HornetError('This console cannot render the live terminal; use --once or --json')
        previous = (kernel, handle, mode.value)
    else:
        import termios
        import tty
        previous = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
    try:
        sys.stdout.write('\x1b[?1049h\x1b[?25l')
        sys.stdout.flush()
        yield
    finally:
        sys.stdout.write('\x1b[?25h\x1b[?1049l')
        sys.stdout.flush()
        if os.name == 'nt':
            previous[0].SetConsoleMode(previous[1], previous[2])
        else:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, previous)


def run(project, args):
    if not 0.25 <= args.interval <= 60:
        raise HornetError('Use a terminal refresh interval between 0.25 and 60 seconds')
    data = hive_status.snapshot(project, args.run_dir, args.run)
    if args.once or args.json:
        return data
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise HornetError('Live mode requires an interactive terminal; use --once or --json')
    view = View()
    updates, stop = Queue(maxsize=1), threading.Event()
    def observe():
        # Slow historical scans never block keyboard navigation or exit.
        while not stop.wait(args.interval):
            try:
                update = hive_status.snapshot(project, args.run_dir, args.run)
            except (HornetError, OSError, ValueError) as exc:
                update = {'ok': False, 'root': str(project.root), 'goals': [], 'cells': [],
                          'active_cells': 0, 'warnings': [str(exc)]}
            try:
                updates.put_nowait(update)
            except Full:
                pass
    observer = threading.Thread(target=observe, name='hornet-terminal-reader', daemon=True)
    with screen():
        observer.start()
        try:
            while True:
                try:
                    data = updates.get_nowait()
                except Empty:
                    pass
                width, height = shutil.get_terminal_size((100, 30))
                sys.stdout.write('\x1b[H\x1b[2J' + '\n'.join(view.lines(data, width - 1, height)))
                sys.stdout.flush()
                if not view.key(read_key(min(args.interval, 1)), data):
                    break
        finally:
            stop.set()
            observer.join(timeout=0.1)
    return {'ok': True, 'terminal_closed': True}


def render_snapshot(data):
    print('HORNET | HIVE COMMAND CENTER')
    print('{} goals | {} cells | {} active'.format(len(data['goals']), len(data['cells']), data['active_cells']))
    for row in data['cells']:
        print(clean('{} / {} [{}] agent={} last={}'.format(row['goal_id'], row['id'], row['status'], row['assigned_agent'] or '(direct)', age(row['last_action']))))
        print('  ' + clean(row['run']))
    for warning in data['warnings']:
        print('Warning: ' + clean(warning))

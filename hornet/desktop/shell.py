"""Focused, dependency-free desktop shell for Hornet Coder.

Workflow rules remain in App; this module only arranges and presents controls.
"""
import tkinter as tk
from tkinter import ttk, font as tkfont

BG = '#10151d'
PANEL = '#18212d'
FIELD = '#0d131c'
TEXT = '#e6edf5'
MUTED = '#99a9bd'
ACCENT = '#f3b84b'


def build_shell(app):
    root = app.root
    from hornet import __version__
    root.title('Hornet Coder | ' + __version__)
    from .app_icon import install_window_icon
    install_window_icon(root)
    root.geometry('1380x900')
    root.minsize(1040, 720)
    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use('clam')
    family = 'Segoe UI' if 'Segoe UI' in tkfont.families(root) else 'TkDefaultFont'
    mono = 'Consolas' if 'Consolas' in tkfont.families(root) else 'TkFixedFont'
    root.option_add('*Font', (family, 10))
    style.configure('.', background=PANEL, foreground=TEXT, font=(family, 10), bordercolor=PANEL)
    style.configure('TFrame', background=BG)
    style.configure('Card.TFrame', background=PANEL)
    style.configure('TLabel', background=BG, foreground=TEXT)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Title.TLabel', font=(family, 20, 'bold'))
    style.configure('Section.TLabel', font=(family, 10, 'bold'), foreground=MUTED)
    # Clam's inherited active/pressed colors and relief are light and beveled.
    # Specify every state, including the border shades, rather than inheriting
    # the theme's raised-button and pale menu-button defaults.
    for name, base, hover, pressed, foreground in (
            ('TButton', PANEL, '#28374b', '#213044', TEXT),
            ('TMenubutton', PANEL, '#28374b', '#213044', TEXT),
            ('Accent.TButton', ACCENT, '#ffc768', '#dda43e', '#171c24')):
        disabled = '#18212b' if name != 'Accent.TButton' else '#293344'
        backgrounds = [('disabled', disabled), ('pressed', pressed), ('active', hover), ('!disabled', base)]
        foregrounds = [('disabled', '#748399'), ('!disabled', foreground)]
        style.configure(name, padding=(12, 8), relief='flat', shiftrelief=0,
                        background=base, foreground=foreground, borderwidth=1,
                        bordercolor=base, lightcolor=base, darkcolor=base,
                        focuscolor=ACCENT, focusthickness=1)
        style.map(name, background=backgrounds, foreground=foregrounds,
                  lightcolor=backgrounds, darkcolor=backgrounds,
                  bordercolor=[('disabled', disabled), ('focus', ACCENT)] + backgrounds,
                  relief=[('disabled', 'flat'), ('!disabled', 'flat')],
                  shiftrelief=[('disabled', 0), ('!disabled', 0)])
    style.configure('Accent.TButton', font=(family, 10, 'bold'))
    # Replace the old horizontal, beveled menu indicator with a text chevron.
    # Preserve the native menubutton role and its keyboard/menu behavior.
    style.layout('TMenubutton', [('Menubutton.border', {'sticky': 'nswe', 'children': [
        ('Menubutton.focus', {'sticky': 'nswe', 'children': [
            ('Menubutton.padding', {'sticky': 'nswe', 'children': [
                ('Menubutton.label', {'sticky': 'nswe'})]})]})]})])
    style.configure('TEntry', fieldbackground=FIELD, foreground=TEXT, padding=7, insertcolor=TEXT)
    style.configure('TCombobox', fieldbackground=FIELD, foreground=TEXT, padding=6, arrowcolor=MUTED)
    style.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)])
    style.configure('TCheckbutton', background=BG, foreground=MUTED, padding=4)
    style.map('TCheckbutton', background=[('active', BG)])
    rowfont = tkfont.Font(root=root, family=family, size=10)
    style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                    rowheight=rowfont.metrics('linespace') + int(root.winfo_fpixels('10p')), borderwidth=0)
    style.map('Treeview', background=[('selected', '#29405b')], foreground=[('selected', '#ffffff')])
    style.configure('TNotebook', background=BG, borderwidth=0)
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(14, 9),
                    borderwidth=1, bordercolor='#304052', lightcolor=BG, darkcolor=BG,
                    focuscolor=ACCENT)
    # Persistent selection, not just keyboard focus: keep it clear after the
    # pointer leaves the tab or focus moves into the editor. Disabled wins.
    tab_background = [('disabled', BG), ('selected', '#34465c'), ('active', '#213044'), ('!disabled', BG)]
    tab_border = [('disabled', '#25303d'), ('selected', ACCENT), ('active', '#40546c'), ('!disabled', '#304052')]
    # Clam otherwise applies smaller selected-only padding, shrinking the tab
    # and moving every tab to its right. Selection changes colors only.
    style.map('TNotebook.Tab', padding=[('selected', (14, 9)), ('!selected', (14, 9))],
              expand=[('selected', (0, 0, 0, 0)), ('!selected', (0, 0, 0, 0))],
              background=tab_background,
              foreground=[('disabled', '#748399'), ('selected', '#ffd583'), ('active', TEXT), ('!disabled', MUTED)],
              bordercolor=tab_border, lightcolor=tab_border, darkcolor=tab_border)

    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=BG, borderwidth=0)
    style.configure('TScrollbar', background='#334155', troughcolor=FIELD, borderwidth=0, arrowsize=12)

    header = ttk.Frame(root, padding=(20, 14, 20, 12))
    header.pack(fill='x')
    ttk.Label(header, text='HORNET', style='Title.TLabel').pack(side='left')
    ttk.Label(header, text='  /  CODER', style='Muted.TLabel').pack(side='left', padx=8)
    app.settings_button = ttk.Button(header, text='Model settings', command=lambda: toggle_panel(app.connection_panel))
    app.settings_button.pack(side='right')
    app.response_sound = tk.BooleanVar(value=app.load_response_sound())
    app.response_sound_button = ttk.Checkbutton(header, text='Reply chime',
        variable=app.response_sound, command=app.save_response_sound)
    app.response_sound_button.pack(side='right', padx=12)
    app.model_badge = tk.StringVar(value='Choose a model in Settings')
    ttk.Label(header, textvariable=app.model_badge, style='Muted.TLabel').pack(side='right', padx=16)

    app.connection_panel = connection = ttk.Frame(root, padding=(20, 0, 20, 12))
    # Pack this only on request; configuration is not part of every mission.
    app.endpoint = tk.StringVar(value='http://localhost:1234/v1')
    app.model = tk.StringVar(value=app.load_saved_model())
    ttk.Label(connection, text='Server').pack(side='left')
    ttk.Entry(connection, textvariable=app.endpoint, width=32).pack(side='left', padx=(8, 18))
    ttk.Label(connection, text='Model').pack(side='left')
    app.models = ttk.Combobox(connection, textvariable=app.model, width=30)
    app.models.pack(side='left', fill='x', expand=True, padx=8)
    app.models.bind('<<ComboboxSelected>>', app.save_model)
    app.models.bind('<FocusOut>', app.save_model)
    app.find_models = ttk.Button(connection, text='Find models', command=app.load_models)
    app.find_models.pack(side='left')
    app.model.trace_add('write', lambda *_: app.model_badge.set(app.model.get().strip() or 'Choose a model in Settings'))
    app.model_badge.set(app.model.get().strip() or 'Choose a model in Settings')

    app.body = body = ttk.Frame(root, padding=(16, 0, 16, 0))
    body.pack(fill='both', expand=True)
    panes = ttk.Panedwindow(body, orient='horizontal')
    panes.pack(fill='both', expand=True)
    left = ttk.Frame(panes, padding=(0, 8, 12, 0), width=285)
    panes.add(left, weight=0)
    nav = ttk.Frame(left)
    nav.pack(fill='x', pady=(0, 10))
    ttk.Label(nav, text='PROJECTS', style='Section.TLabel').pack(side='left')
    new = ttk.Menubutton(nav, text='+ New \u25be')
    new.pack(side='right')
    menu = tk.Menu(new, tearoff=False)
    app.new_menu_button = new
    app.new_project_button = ttk.Button(left, command=app.new_project)
    app.import_button = ttk.Button(left, command=lambda: app.new_project(True))
    menu.add_command(label='New project...', command=app.new_project_button.invoke)
    menu.add_command(label='Import folder...', command=app.import_button.invoke)
    new.configure(menu=menu)
    tree_frame = ttk.Frame(left)
    tree_frame.pack(fill='both', expand=True)
    app.projects = ttk.Treeview(tree_frame, show='tree', selectmode='browse', height=14)
    app.projects.column('#0', width=285, minwidth=180, stretch=True)
    sy = ttk.Scrollbar(tree_frame, orient='vertical', command=app.projects.yview)
    sx = ttk.Scrollbar(tree_frame, orient='horizontal', command=app.projects.xview)
    app.projects.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
    sy.pack(side='right', fill='y')
    sx.pack(side='bottom', fill='x')
    app.projects.pack(fill='both', expand=True)
    app.projects.bind('<<TreeviewSelect>>', app.choose_revision)
    app.projects.bind('<Button-3>', app.revision_context_menu)
    project_footer = ttk.Frame(left)
    project_footer.pack(side='bottom', fill='x', before=tree_frame)
    ttk.Label(project_footer, text='Create a working revision to edit.', style='Muted.TLabel').pack(anchor='w', pady=(10, 4))
    app.project_menu_button = ttk.Menubutton(project_footer, text='Project actions \u25be')
    app.project_menu_button.pack(fill='x', pady=(2, 10))
    app.revision_menu_button = app.project_menu_button
    app.project_menu = tk.Menu(app.project_menu_button, tearoff=False)
    app.project_menu_button.configure(menu=app.project_menu)
    app.project_menu.configure(postcommand=lambda: fill_project_menu(app))

    main = ttk.Frame(panes, padding=(10, 0, 0, 0))
    panes.add(main, weight=1)
    context = ttk.Frame(main, padding=(0, 5, 0, 10))
    context.pack(fill='x')
    app.context_title = tk.StringVar(value='Your coding workspace')
    ttk.Label(context, textvariable=app.context_title, font=(family, 13, 'bold')).pack(anchor='w')
    app.workflow_hint = tk.StringVar(value='Create or import a project to get started.')
    ttk.Label(context, textvariable=app.workflow_hint, style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
    app.context_actions = ttk.Frame(main)
    app.context_actions.pack(fill='x', pady=(0, 8))
    app.init_button = ttk.Button(app.context_actions, text='Understand project', command=app.initialize_project, style='Accent.TButton')
    app.accept_map = ttk.Button(app.context_actions, text='Looks right, continue', command=app.accept_project_map, state='disabled', style='Accent.TButton')
    app.edit_map = ttk.Button(app.context_actions, text='Correct understanding', command=app.edit_project_map, state='disabled')
    app.attention_buttons = {}
    for action, label in [('resume', 'Resume'), ('new', 'Keep partial / new mission...'), ('discard', 'Discard working revision...')]:
        app.attention_buttons[action] = ttk.Button(app.context_actions, text=label,
            command=lambda choice=action: app.attention_action(choice))
    # Menu-backed controls retain the controller's existing state gates.
    app.copy_button = ttk.Button(main, command=app.copy_revision)
    app.delete_project_button = ttk.Button(main, command=app.delete_project)
    app.run_button = ttk.Button(main, command=app.run_project)
    app.blueprint_button = ttk.Button(main, command=app.edit_blueprint)

    editor = ttk.Panedwindow(main, orient='horizontal')
    editor.pack(fill='both', expand=True)
    files_panel = ttk.Frame(editor, padding=(0, 0, 10, 0), width=190)
    editor.add(files_panel, weight=0)
    ttk.Label(files_panel, text='FILES', style='Section.TLabel').pack(anchor='w', pady=(2, 8))
    app.add_to_prompt_button = ttk.Button(files_panel, text='Add to prompt', command=app.add_files_to_prompt)
    app.add_to_prompt_button.pack(fill='x', pady=(0, 6))
    app.files = ttk.Treeview(files_panel, show='tree', selectmode='extended', height=8)
    app.files.column('#0', width=180, minwidth=100, stretch=True)
    fs = ttk.Scrollbar(files_panel, orient='vertical', command=app.files.yview)
    fx = ttk.Scrollbar(files_panel, orient='horizontal', command=app.files.xview)
    app.files.configure(yscrollcommand=fs.set, xscrollcommand=fx.set)
    fs.pack(side='right', fill='y')
    fx.pack(side='bottom', fill='x')
    app.files.pack(fill='both', expand=True)
    app.files.bind('<<TreeviewSelect>>', app.preview)
    app.files.bind('<Button-3>', app.files_context_menu)
    app.files.bind('<Shift-F10>', app.files_context_menu)
    app.files.bind('<Menu>', app.files_context_menu)
    viewer = ttk.Frame(editor)
    editor.add(viewer, weight=1)
    app.tabs = ttk.Notebook(viewer)
    app.tabs.pack(fill='both', expand=True)
    app.views = {}
    for label, display in [('Source', 'Code'), ('Shadow', 'Context'), ('Blueprint', 'Understanding'),
                           ('Proposal', 'Review'), ('Run output', 'Tests'), ('Activity', 'Activity')]:
        frame = ttk.Frame(app.tabs)
        app.tabs.add(frame, text=display)
        text = tk.Text(frame, wrap='word' if label == 'Proposal' else 'none', background=FIELD,
                       foreground=TEXT, insertbackground=TEXT, selectbackground='#29405b',
                       font=(mono, 11), padx=16, pady=14, relief='flat', borderwidth=0,
                       highlightthickness=0)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=text.xview)
        text.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        scroll.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        text.pack(fill='both', expand=True)
        text.configure(state='disabled')
        app.views[label] = text

    mission = ttk.Frame(main, padding=(0, 14, 0, 10))
    mission.pack(side='bottom', fill='x', before=editor)
    ttk.Label(mission, text='WHAT SHOULD CHANGE?', style='Section.TLabel').pack(anchor='w')
    app.prompt_resize_handle = ttk.Label(mission, text='Resize prompt: drag up/down or use arrow keys',
        style='Muted.TLabel', cursor='sb_v_double_arrow', takefocus=True, anchor='e')
    app.prompt_resize_handle.pack(fill='x')
    prompt_field = ttk.Frame(mission)
    prompt_field.pack(fill='x', pady=(8, 8))
    app.goal = tk.Text(prompt_field, height=3, font=(family, 11), background=FIELD, foreground=TEXT,
                       insertbackground=TEXT, selectbackground='#29405b', relief='flat', padx=12,
                       pady=10, highlightthickness=1, highlightbackground='#2b3a4e', highlightcolor=ACCENT, undo=True)
    app.goal_scrollbar = ttk.Scrollbar(prompt_field, orient='vertical', command=app.goal.yview)
    app.goal.configure(yscrollcommand=app.goal_scrollbar.set)
    app.goal_scrollbar.pack(side='right', fill='y')
    app.goal.pack(side='left', fill='x', expand=True)
    from .prompt_resize import PromptResize
    app.prompt_resize = PromptResize(app.goal, editor, app.prompt_resize_handle)
    edit_menu = tk.Menu(app.goal, tearoff=False)
    for label, command in [('Copy', app.copy_mission), ('Paste', app.paste_mission), ('Clear', app.clear_mission)]:
        edit_menu.add_command(label=label, command=command)
    app.goal.bind('<Button-3>', lambda event: edit_menu.tk_popup(event.x_root, event.y_root))
    app.new_files = tk.StringVar()
    app.include_run = tk.BooleanVar(value=False)
    app.check_command = tk.StringVar(value='')
    app.options_panel = options = ttk.Frame(mission)
    app.confirm_plan = tk.BooleanVar(value=True)
    ttk.Checkbutton(options, text='Review agent plan before execution', variable=app.confirm_plan).pack(anchor='w', pady=(0, 4))
    ttk.Label(options, text='Default: Python/JSON syntax checks on changed files. You check functionality.', style='Muted.TLabel').pack(anchor='w')
    ttk.Label(options, text='Extra VM check command (optional, runs only when supplied)', style='Muted.TLabel').pack(anchor='w')
    ttk.Entry(options, textvariable=app.check_command).pack(fill='x', pady=(4, 4))
    ttk.Checkbutton(options, text='Include latest VM output', variable=app.include_run).pack(anchor='w')
    app.mission_actions = actions = ttk.Frame(mission)
    actions.pack(fill='x')
    app.options_button = ttk.Button(actions, text='Mission options', command=lambda: toggle_panel(options, before=actions))
    app.options_button.pack(side='left')
    app.diagnose_button = ttk.Button(actions, text='Diagnose', command=app.run_diagnosis)
    app.diagnose_button.pack(side='left', padx=8)
    app.auto_button = ttk.Button(actions, text='Run mission', command=app.run_mission, style='Accent.TButton')
    app.auto_button.pack(side='right')
    app.accept_mission_button = ttk.Button(actions, text='Accept reviewed changes', command=app.accept_automated_mission,
                                          state='disabled', style='Accent.TButton')
    app.counter_plan_button = ttk.Button(actions, text='Counter plan', command=app.counter_saved_plan)
    app.approve_plan_button = ttk.Button(actions, text='Approve plan & continue', command=app.approve_saved_plan, style='Accent.TButton')
    app.stop_button = ttk.Button(actions, text='Stop query', command=app.stop_query)
    app.review_button = ttk.Button(actions, text='Review pending work', command=app.review_pending)
    app.review_button.pack(side='right', padx=8)
    app.connection_notice = tk.StringVar()
    def update_notice(*_):
        app.connection_notice.set('Source and context are sent to ' + app.endpoint.get().strip())
    app.endpoint.trace_add('write', update_notice)
    update_notice()
    ttk.Label(mission, textvariable=app.connection_notice, style='Muted.TLabel').pack(anchor='w', pady=(8, 0))
    app.send = ttk.Button(connection, command=app.prepare, state='disabled')
    app.apply_button = ttk.Button(connection, command=app.apply, state='disabled')
    from .status_light import StatusLight
    app.activity_indicator = StatusLight(root)
    app.activity_indicator.pack(side='bottom', fill='x', padx=16, before=body)
    app.status = tk.StringVar(value='Ready. Project code runs only in the configured Test VM.')
    app.status_label = ttk.Label(root, textvariable=app.status, padding=(20, 8), style='Muted.TLabel', wraplength=1200)
    app.status_label.pack(side='bottom', fill='x', before=app.activity_indicator)
    root.bind('<Control-Return>', lambda event: run_mission_shortcut(app))
    app.connection_panel._before = body



def run_mission_shortcut(app):
    """Only invoke the visible run action; never replace pending review work."""
    button = app.auto_button
    if not app.busy and button.winfo_manager() and str(button.cget('state')) == 'normal':
        button.invoke()
    return 'break'


def toggle_panel(panel, before=None):
    if panel.winfo_manager():
        panel.pack_forget()
    else:
        panel.pack(fill='x', before=before or getattr(panel, '_before', None))


def fill_project_menu(app):
    menu = app.project_menu
    menu.delete(0, 'end')
    for label, button in [('Understand project', app.init_button), ('Correct pending understanding', app.edit_map),
                          ('Project understanding', app.blueprint_button), ('Create working revision', app.copy_button),
                          ('Run in Test VM', app.run_button)]:
        menu.add_command(label=label, command=button.invoke, state=str(button.cget('state')))
    selected = bool(app.project and app.revision and not app.busy)
    from .working_revision import is_working
    working = selected and is_working(app.ws, app.project, app.revision)
    menu.add_command(label='Discard working revision...', command=app.discard_working_revision,
                     state='normal' if working else 'disabled')
    menu.add_command(label='Lock revision', command=app.lock_revision, state='normal' if working else 'disabled')
    menu.add_command(label='Clean up checkpoints...', command=lambda: app.clean_checkpoints(app.project, app.revision),
                     state='normal' if selected and not working else 'disabled')
    menu.add_command(label='Hide internal checkpoints' if getattr(app, 'show_checkpoints', False)
                     else 'Show internal checkpoints', command=app.toggle_checkpoints,
                     state='disabled' if app.busy else 'normal')
    from .understanding import show_details
    menu.add_command(label='Understanding details', command=lambda: show_details(app.root, app.pending_initialization),
                     state='normal' if app.pending_initialization and not app.busy else 'disabled')
    menu.add_separator()
    for label, command in [('Rename revision...', lambda: app.rename_revision(app.project, app.revision)),
                           ('Save revision to disk...', lambda: app.export_revision(app.project, app.revision)),
                           ('Move revision to trash...', app.trash_revision)]:
        menu.add_command(label=label, command=command, state='normal' if selected else 'disabled')
    menu.add_separator()
    menu.add_command(label='Delete project...', command=app.delete_project_button.invoke,
                     state=str(app.delete_project_button.cget('state')))

    menu.add_separator()
    menu.add_command(label='Goals and pointers', command=app.show_goal_pointers, state='normal' if selected else 'disabled')
    menu.add_command(label='Resume interrupted mission', command=app.resume_saved_mission, state='normal' if working else 'disabled')
    menu.add_separator()
    menu.add_command(label='Open Projects Folder', command=app.open_projects_folder)
    menu.add_command(label='Set Projects Folder', command=app.set_projects_folder,
                     state='disabled' if app.busy else 'normal')


def sync_shell(app, project_ready, revision_ready):
    app.project_menu_button.configure(state='normal')
    app.stop_button.pack_forget()
    app.approve_plan_button.pack_forget()
    app.counter_plan_button.pack_forget()
    if app.busy and app.cancel_token is not None:
        app.stop_button.configure(state='disabled' if app.cancel_token.event.is_set() else 'normal')
        app.stop_button.pack(side='right', padx=8)
    if app.busy:
        app.activity_indicator.start()
    else:
        app.activity_indicator.stop()
    from .working_revision import is_working
    working = bool(app.project and app.revision and is_working(app.ws, app.project, app.revision))
    app.context_title.set((app.project + '  /  ' + app.revision) if app.project and app.revision else 'Your coding workspace')
    app.new_menu_button.configure(state='disabled' if app.busy else 'normal')
    draft = (app.pending_initialization and app.pending_initialization.get('project') == app.project
             and app.pending_initialization.get('revision') == app.revision)
    app.init_button.configure(text='Understand project')
    for button in (app.init_button, app.accept_map, app.edit_map, app.accept_mission_button):
        button.pack_forget()
    if app.busy:
        hint = 'Working on the captured revision. Review progress in Activity.'
    elif draft:
        from .understanding import draft_complete, draft_status
        hint = draft_status(app.pending_initialization)
        if project_ready:
            hint += ' The previously accepted understanding is unchanged.'
        if draft_complete(app.pending_initialization):
            app.accept_map.pack(side='left')
            app.edit_map.pack(side='left', padx=8)
        else:
            app.init_button.configure(text='Resume understanding')
            app.init_button.pack(side='left')
    elif app.project and app.revision and not project_ready:
        hint = 'Understand this project before running a mission.'
        app.init_button.pack(side='left')
    elif app.project and not revision_ready:
        hint = 'Choose an initialized revision to run a mission.'
    elif revision_ready and not working:
        hint = 'This revision is locked. Choose Project actions > Create working revision to run missions.'
    elif revision_ready:
        hint = 'Describe a change, run a mission, then review before accepting.'
    else:
        hint = 'Create or import a project to get started.'
    from .attention import current
    attention = current(app)
    for button in getattr(app, 'attention_buttons', {}).values():
        button.pack_forget()
    if attention:
        hint = 'Needs attention. What do you want to do here?'
        for action, button in app.attention_buttons.items():
            button.configure(state='disabled' if action == 'resume' and not attention['can_resume'] else 'normal')
            button.pack(side='left', padx=(0, 8))
    app.workflow_hint.set(hint)
    app.review_button.configure(state='normal' if app.project and not app.busy else 'disabled')
    plan_ready = (not app.busy and working and app.pending_mission and app.pending_mission.get('state') == 'plan_review'
                  and (app.pending_mission.get('project'), app.pending_mission.get('base_revision')) == (app.project, app.selected_mission_base()))
    if plan_ready:
        app.auto_button.pack_forget()
        app.approve_plan_button.pack(side='right')
        app.counter_plan_button.pack(side='right', padx=8)
    elif str(app.accept_mission_button.cget('state')) == 'normal':
        app.auto_button.pack_forget()
        app.accept_mission_button.pack(side='right')
    elif not app.auto_button.winfo_manager():
        app.auto_button.pack(side='right')

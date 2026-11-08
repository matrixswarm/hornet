"""Execute a validated semantic plan one durable, scoped leaf at a time."""
from . import automation as api
from .mission_plan import leaf_goals
from .revision_goals import read_revision_goals
from .workspace import read_blueprint, read_source


def ordered_leaves(goals):
    leaves = leaf_goals(goals)
    result, done = [], set()
    def add(identifier):
        if identifier in done:
            return
        for dependency in leaves[identifier]['depends_on']:
            add(dependency)
        done.add(identifier)
        result.append(leaves[identifier])
    for identifier in leaves:
        add(identifier)
    return result


def execute_leaves(ws, project, base, mission, goals, checkpoint, propose, verify_proposal,
                   publication, current, record, save):
    from .revision_journal import inject, publication_entry
    journal_steps = []
    candidate = base
    receipts, links, summaries, prior = [], {}, [], []
    results = []
    base_hashes = api.inventory(ws.revision(project, base))
    for leaf in ordered_leaves(goals):
        identifier = leaf['id']
        parent = candidate
        root = ws.revision(project, parent)
        if parent != base:
            read_revision_goals(root)
        before = api.inventory(root)
        blueprint = read_blueprint(root)
        existing = [name for name in leaf['files'] if name in before]
        new = [name for name in leaf['files'] if name not in before]
        leaf_mission = ('Original mission: ' + mission + '\nCurrent leaf goal (' + identifier + '): '
                        + leaf['goal'] + '\nRequired evidence: ' + leaf['success_criteria'])
        from .plan_context import mission_context as scoped_context
        context = scoped_context(ws, project, parent, existing, leaf_mission, new,
            context_files=record.get('plan', {}).get('context_files', []), scoped_shadows=True)
        context.update(goal_decomposition=goals, active_goal_id=identifier, prior_goal_edits=list(prior))
        inject(checkpoint.identity, context, journal_steps)
        if record.get('diagnostic') is not None:
            context['run_diagnostic'] = record['diagnostic']
        path = 'goals/' + identifier + '/proposal'
        current(path, 'working on goal ' + identifier + ': ' + leaf['goal'])

        def unchanged_parent():
            if api.inventory(root) != before or read_blueprint(root) != blueprint:
                raise ValueError('A leaf input revision changed; inspect it before continuing.')

        def verify(value):
            unchanged_parent()
            verify_proposal(value, set(leaf['files']))
            if not value[0]['files']:
                raise ValueError('The agent returned no source changes for leaf goal ' + identifier + '.')
            if any(ids != [identifier] for ids in value[0]['goal_links'].values()):
                raise ValueError('A leaf proposal may address only its current active goal.')

        proposal, leaf_receipts = checkpoint.phase(path, leaf['goal'],
            {'revision': parent, 'source_sha256': before, 'context': context},
            lambda key, digest: propose(root, context), read_only=True, verify=verify)
        unchanged_parent()
        candidate = publication('goals/' + identifier + '/publish', parent,
            {'base': parent, 'source_sha256': before, 'proposal': proposal, 'context': context},
            lambda: ws.fork(project, parent, proposal['files'], 'Goal ' + identifier + ': ' + leaf['goal'],
                            proposal=proposal, expected_context=context))
        result_root = ws.revision(project, candidate)
        after = api.inventory(result_root)
        expected_names = set(before) | set(proposal['files'])
        if (set(after) != expected_names
                or any(after.get(name) != digest for name, digest in before.items() if name not in proposal['files'])
                or any(read_source(result_root, name) != content for name, content in proposal['files'].items())):
            raise ValueError('Published leaf artifacts do not match the scoped proposal; inspect this revision.')
        for name, identifiers in proposal['goal_links'].items():
            links[name] = sorted(set(links.get(name, [])) | set(identifiers))
        receipts.extend(leaf_receipts)
        summaries.append(identifier + ': ' + proposal['summary'])
        prior.append(identifier)
        if 'revision_journal' in checkpoint.identity:
            entry = publication_entry(ws, project, candidate)
            entry.update(goal_id=identifier, agent_summary_unverified=proposal['summary'][:400])
            journal_steps.append(entry)
        results.append({'id': identifier, 'revision': candidate, 'goal': leaf['goal'],
                        'success_criteria': leaf['success_criteria'], 'status': 'pending_human_review',
                        'artifact_sha256': {name: after[name] for name in proposal['files']}})
        record.update(leaf_results=list(results), tool_receipts=list(receipts), goal_links=dict(links))
        save()
    root = ws.revision(project, candidate)
    final_hashes = api.inventory(root)
    changed = {name: read_source(root, name) for name, digest in final_hashes.items()
               if base_hashes.get(name) != digest}
    if not changed:
        raise ValueError('The leaf plan produced no net source changes; inspect its artifacts.')
    proposal = {'summary': '\n'.join(summaries), 'files': changed,
                'goal_links': {name: links[name] for name in changed}}
    return candidate, proposal, receipts

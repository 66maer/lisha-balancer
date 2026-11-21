"""Verify who each spoken phrase addresses and score the complete command."""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import combinations, groupby

from .solver import BuffState, IllegalAction, PlayerAction, receive_buff
from .xinfa import catalogue, guide_cost


@dataclass(frozen=True, slots=True)
class Call:
    text: str
    players: frozenset[str]
    effort: int
    team_mentions: int = 0


_MOVEMENT = {'fan': 1, 'guide': 1, 'single': 2, 'double': 5, 'triple': 9}
_VERBS = {'fan': '吃扇', 'guide': '引导', 'single': '蹭圈', 'double': '蹭2圈', 'triple': '蹭3圈'}
_CHINESE = '零一二三四五六七八九十'


def _quantity(count):
    return ('两' if count == 2 else _CHINESE[count] if count <= 10 else str(count)) + '个'


def _buff(value):
    if value == 0:
        return '无煞气的'
    return f'{_CHINESE[abs(value)]}层{"赤" if value > 0 else "幽"}煞的'


def _team_phrase(teams):
    teams = sorted(teams)
    return '和'.join(f'{team}队' for team in teams) if len(teams) <= 2 else '、'.join(map(str, teams)) + '队'


def _numbered(keys):
    return '、'.join(f'{group}队{"、".join(key[-1] for key in slots)}号'
                    for group, slots in groupby(sorted(keys), key=lambda key: key[1]))


def _command_states(players, plan):
    """Read Buffs at the start of each set of player instructions."""
    initial = {p.player_key: BuffState((p.stacks or 0) * (-1 if p.color == 'black' else 1), p.isolated)
               for p in players if p.alive}
    if not plan.current_snapshot and plan.snapshot_stage == 'before_tank':
        for player in players:
            if player.is_tank and player.alive:
                key = player.player_key
                initial[key] = receive_buff(initial[key], 'red', 3).state
    after_fan = dict(initial)
    for action in plan.actions:
        if action.fan:
            key = action.player_key
            after_fan[key] = receive_buff(initial[key], 'red').state
    return initial, after_fan


def buff_instruction(players, plan):
    """Group a verified plan's actual recipients by Buff and action."""
    initial, after_fan = _command_states(players, plan)
    centers = set(plan.centers)
    fans = Counter(initial[action.player_key] for action in plan.actions if action.fan)
    guides = Counter(after_fan[key] for key in plan.centers)
    helpers = defaultdict(Counter)
    for action in plan.actions:
        if action.circles and action.player_key not in centers:
            helpers[len(action.circles)][after_fan[action.player_key]] += 1

    def describe(counts, verb):
        groups = []
        for state in sorted(counts, key=lambda item: (-item.value, item.isolated)):
            value = state.value
            subject = f'{{{"红" if value > 0 else "黑"}{abs(value)}}}' if value else '无煞'
            if state.isolated:
                subject += '隔'
            groups.append(f'{subject}×{counts[state]}')
        return ' '.join(groups) + ' ' + verb if groups else ''

    return '\n'.join((
        describe(fans, '吃扇'),
        describe(guides, '引导'),
        '，'.join(describe(helpers[hits], _VERBS[('single', 'double', 'triple')[hits - 1]])
                 for hits in sorted(helpers)),
    ))


class CallPlanner:
    def __init__(self, players):
        self.players = {player.player_key: player for player in players if player.alive}
        specs = catalogue()
        self.specs = {key: specs.get(p.xinfa) for key, p in self.players.items() if p.xinfa}
        self.nicknames = {spec.nickname for spec in specs.values()}
        self.healing_schools = {spec.school for spec in specs.values() if spec.role == 'healer'}
        self.teams = {key: int(key[1]) for key in self.players}
        self._calls = {}
        self._fan_calls = {}

    def _scoped_fan_calls(self, initial, current_snapshot):
        """Resolve team calls with an explicit exclusion for black stacks of three or more."""
        signature = current_snapshot, tuple(sorted(initial.items()))
        if signature in self._fan_calls:
            return self._fan_calls[signature]
        excluded = {key for key, state in initial.items() if state.value <= -3}
        blocked = {key for key, state in initial.items() if state.isolated or state.value >= 4
                   or (not current_snapshot and self.players[key].is_tank)}
        by_team, by_name = defaultdict(set), defaultdict(set)
        for key, team in self.teams.items():
            by_team[team].add(key)
            spec = self.specs.get(key)
            if spec:
                by_name[spec.nickname].add(key)
        extras = {}
        if all(self.specs.get(key) for key in self.players):
            for name, keys in by_name.items():
                if len(keys) == 1:
                    key, = keys
                    if key not in blocked | excluded:
                        extras[key] = name
        calls = {}

        def add(keys, label, effort):
            keys = frozenset(keys)
            call = Call(label + '吃扇，三层及以上幽煞不吃', keys, effort)
            if keys not in calls or _call_score((call,)) < _call_score((calls[keys],)):
                calls[keys] = call

        if excluded:
            teams = sorted(by_team)
            for count in range(2, len(teams) + 1):
                for scope in combinations(teams, count):
                    members = set().union(*(by_team[team] for team in scope))
                    selected = members - excluded
                    if not selected or selected & blocked or not members & excluded:
                        continue
                    consecutive = scope[-1] - scope[0] + 1 == len(scope)
                    label = ('' if consecutive else '、').join(_CHINESE[team] for team in scope) + '队'
                    effort = 2 if consecutive else 4
                    add(selected, label, effort)
                    for key in sorted(extras.keys() - members):
                        add(selected | {key}, label + '和' + extras[key], effort + 1)
        self._fan_calls[signature] = calls
        return calls

    def fan_variants(self, plan):
        if plan.mode == 'circle':
            return
        initial, _ = _command_states(self.players.values(), plan)
        original = {action.player_key for action in plan.actions if action.fan}
        black_two = {key for key, state in initial.items() if state.value == -2} - original
        if not black_two:
            return
        helpers = {action.player_key: action.circles for action in plan.actions
                   if action.circles and action.player_key not in plan.centers}
        scopes = [targets for targets in self._scoped_fan_calls(initial, plan.current_snapshot)
                  if targets & black_two]
        if not scopes:
            return
        fans = {key: receive_buff(initial[key], 'red').state for key in set().union(*scopes)}
        spares = sorted(initial.keys() - helpers.keys() - set(plan.centers),
                        key=lambda key: (guide_cost(self.players[key]), key))
        for targets in scopes:
            # The new fan list can also make a complete three-person Buff group
            # available for guidance. Preserve the helpers' circle assignments.
            after_fan = {key: fans[key] if key in targets else state
                         for key, state in initial.items()}
            by_buff = defaultdict(list)
            for key, state in after_fan.items():
                by_buff[state.value].append(key)
            center_choices = [plan.centers]
            center_choices.extend(tuple(sorted(keys)) for _, keys in sorted(by_buff.items())
                                  if len(keys) == 3 and not helpers.keys() & set(keys))
            changed_centers = set(plan.centers) & (original ^ targets)
            if changed_centers:
                # Try one substitute per headcount effect if the old guide's
                # result may have changed. Prefer ranged and healers.
                substitutes = {}
                for key in spares:
                    state = after_fan[key]
                    try:
                        event = receive_buff(state, 'black')
                    except IllegalAction:
                        continue
                    if not event.strong:
                        changes_count = event.applied and state.value in (0, 1)
                        substitutes.setdefault(changes_count, key)
                        if len(substitutes) == 2:
                            break
                center_choices.extend(tuple(spare if key == changed else key for key in plan.centers)
                                      for changed in sorted(changed_centers) for spare in substitutes.values())
            for centers in center_choices:
                circles = {**helpers, **{key: (order,) for order, key in enumerate(centers, 1)}}
                yield replace(plan, centers=centers, actions=tuple(
                    PlayerAction(key, key in targets, circles.get(key, ()))
                    for key in sorted(targets | circles.keys())
                ))

    def groups(self, selected, states, eligible, *, nickname=None):
        selected = frozenset(selected)
        eligible = frozenset(eligible)
        signature = selected, eligible, tuple(sorted((key, states[key].value) for key in eligible)), nickname
        if signature in self._calls:
            return self._calls[signature]
        if nickname is None:
            schools = defaultdict(lambda: defaultdict(set))
            for key in selected:
                spec = self.specs.get(key)
                if spec and spec.school in self.healing_schools:
                    schools[spec.school][spec.nickname].add(key)
            split = {name: keys for hearts in schools.values() if len(hearts) > 1 for name, keys in hearts.items()}
            # A nickname call must distinguish healing and damage specs.
            # A full Buff group can address players from several schools.
            if split and selected == frozenset().union(*split.values()):
                calls = tuple(
                    call for name, keys in split.items()
                    for call in self.groups(keys, states, eligible, nickname=name)
                )
                result = tuple(sorted(calls, key=lambda call: min(call.players)))
                self._calls[signature] = result
                return result
        choices = {}
        named_choices = {}

        def add(keys, text, effort, *, team_mentions=0, named=False):
            mask = frozenset(keys)
            if mask and mask <= selected:
                call = Call(text, mask, effort, team_mentions)
                prior = choices.get(mask)
                if prior is None or _call_score((call,)) < _call_score((prior,)):
                    choices[mask] = call
                if named:
                    prior = named_choices.get(mask)
                    if prior is None or _call_score((call,)) < _call_score((prior,)):
                        named_choices[mask] = call

        by_buff, by_team, by_name = defaultdict(set), defaultdict(set), defaultdict(set)
        for key in sorted(eligible):
            by_buff[states[key].value].add(key)
            by_team[self.teams[key]].add(key)
            spec = self.specs.get(key)
            if spec:
                by_name[spec.nickname].add(key)
                if spec.school not in self.nicknames and spec.school not in self.healing_schools:
                    by_name[spec.school].add(key)
        if nickname is None:
            for value, members in by_buff.items():
                add(members, _buff(value), 2 if len(members) == 1 else 1)
            for team, members in by_team.items():
                add(members, f'{team}队', 2 if len(members) == 1 else 1, team_mentions=1)
                for value, match in by_buff.items():
                    add(members & match, f'{team}队{_buff(value)}', 2, team_mentions=1)

        all_known = all(self.specs.get(key) for key in eligible)
        known_teams = {team for team, members in by_team.items() if all(self.specs.get(key) for key in members)}
        known_buffs = {value for value, members in by_buff.items() if all(self.specs.get(key) for key in members)}
        for name, members in by_name.items():
            if nickname is not None and name != nickname:
                continue
            specificity = 0 if name in self.nicknames else 1
            if all_known:
                text = name if len(members) == 1 else _quantity(len(members)) + name
                add(members, text, 1 + specificity, named=True)
            for value in sorted(known_buffs):
                add(members & by_buff[value], _buff(value) + name, 2 + specificity, named=True)
            scope = {team for team in known_teams if members & by_team[team] and (members & by_team[team]) <= selected}
            for team in sorted(scope):
                add(members & by_team[team], f'{team}队{name}', 1 + specificity, team_mentions=1)
            if len(scope) > 1:
                scope_effort = 1 if len(scope) <= 3 else 2
                add({key for key in members if self.teams[key] in scope}, _team_phrase(scope) + name,
                    scope_effort + specificity, team_mentions=len(scope))

        names = sorted(named_choices.values(), key=lambda call: (-len(call.players), *_call_score((call,))))

        @lru_cache(maxsize=None)
        def cover(remaining, budget):
            if not remaining:
                return ()
            uncalled = remaining
            fallback = []
            for call in names:
                if call.players <= uncalled:
                    fallback.append(call)
                    uncalled -= call.players
            if uncalled:
                fallback.append(Call(_numbered(uncalled) + (nickname or ''), uncalled, 1 + 2 * len(uncalled),
                                     len({self.teams[key] for key in uncalled})))
            best = tuple(fallback)
            if budget:
                for keys, call in choices.items():
                    if keys <= remaining:
                        candidate = (call, *cover(remaining - keys, budget - 1))
                        if _call_score(candidate) < _call_score(best):
                            best = candidate
            return best

        result = tuple(sorted(cover(selected, 2), key=lambda call: min(call.players)))
        self._calls[signature] = result
        return result

    def compile(self, plan):
        initial, after_fan = _command_states(self.players.values(), plan)
        targets = defaultdict(set)
        centers = set(plan.centers)
        targets['guide'] = centers
        for action in plan.actions:
            key = action.player_key
            if action.fan:
                targets['fan'].add(key)
            if action.circles and key not in centers:
                targets[('single', 'double', 'triple')[len(action.circles)-1]].add(key)
        lines = defaultdict(list)
        groups = []
        effort = 0
        team_mentions = 0
        fan_call = None
        for kind, verb in _VERBS.items():
            if not targets[kind]:
                continue
            calls = self.groups(targets[kind], initial if kind == 'fan' else after_fan, self.players)
            line = '、'.join(call.text for call in calls) + verb
            if kind == 'fan':
                scoped = self._scoped_fan_calls(initial, plan.current_snapshot).get(frozenset(targets[kind]))
                if scoped and _call_score((scoped,)) < _call_score(calls):
                    calls = (scoped,)
                    line = scoped.text
                    fan_call = scoped
            elif kind == 'guide' and fan_call and len(calls) == 1:
                guide, = calls
                if any(guide.text == _buff(after_fan[key].value) and initial[key].value != after_fan[key].value
                       for key in guide.players):
                    line = '扇后' + line
            lines[kind if kind in ('fan', 'guide') else 'circle'].append(line)
            effort += sum(call.effort + _MOVEMENT[kind] for call in calls)
            team_mentions += sum(call.team_mentions for call in calls)
            groups.extend(dict(action=kind, label=call.text, keys=sorted(call.players)) for call in calls)
        text = '\n'.join('，'.join(lines[key]) for key in ('fan', 'guide', 'circle'))
        moves = sum(guide_cost(self.players[key]) for key in centers)
        difficulty = sum(max(0, _MOVEMENT[kind] - 1) * len(targets[kind]) for kind in _MOVEMENT)
        excess = max(0, len(groups)-3)
        total = effort + difficulty + 5 * excess + (moves if plan.mode == 'circle' else 0)
        return text, groups, (total, excess, difficulty, len(groups), team_mentions, moves,
                              sum(len(group) for group in targets.values()))

    def refine(self, plan, rank):
        """Try interchangeable players so a natural spoken group is available."""
        peers = defaultdict(list)
        for key, player in self.players.items():
            if not player.is_tank:
                peers[(player.color, player.stacks, player.isolated)].append(key)
        actions = {action.player_key: action for action in plan.actions}
        centers = set(plan.centers)
        signatures = {key: (action.fan, len(action.circles), key in centers) for key, action in actions.items()}
        best, best_score = plan, rank(plan)
        for keys in peers.values():
            for left, right in combinations(keys, 2):
                if signatures.get(left) == signatures.get(right):
                    continue
                mapping = {left: right, right: left}
                other = replace(plan, centers=tuple(mapping.get(key, key) for key in plan.centers), actions=tuple(
                    replace(action, player_key=mapping.get(action.player_key, action.player_key))
                    for action in plan.actions))
                score = rank(other)
                if score < best_score:
                    best, best_score = other, score
        return best


def _call_score(groups):
    return (sum(call.effort for call in groups), len(groups), sum(call.team_mentions for call in groups),
            sum(len(call.text) for call in groups))

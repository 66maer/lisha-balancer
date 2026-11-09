"""Prefer whole Buff groups that can follow the same short voice instruction.

Three numbered guides are exceptions to group commands: each takes only its
own strike. Other players take one circle at guide 1, or move from 1 to 3.
No classification or geometry inference is performed here.
"""
from collections import defaultdict
from dataclasses import replace

from .solver import (BuffState, BuffEvent, IllegalAction, PlayerAction, SolverPlan,
                     SearchResult, receive_buff, simulate_plan, solve)


def _group_options(members, state, mode, positions, size):
    count=len(members)
    choices=[(False,0)]
    if not state.isolated:
        if mode!='fan':choices.extend(((False,1),(False,2)))
        if mode!='circle' and state.value<0:choices.append((True,0))
    for centers in range(min(3,count)+1):
        try:
            guide=receive_buff(state,'black') if centers else BuffEvent(state,False)
            if centers and mode=='fan' and not (state.isolated or -3<=state.value<=-1):continue
        except IllegalAction:
            continue
        guides=tuple(p.player_key for p in members[:centers])
        helpers=members[centers:]
        for fan,hits in choices:
            if not helpers and (fan or hits):continue
            try:
                event=receive_buff(state,'red') if fan else BuffEvent(state,False)
                if fan and not event.applied:continue
                current=event.state;strong=event.strong;weak=event.weak
                for _ in range(hits):
                    event=receive_buff(current,'black')
                    if not event.applied:raise IllegalAction('隔绝不能作为有效贴圈对象。')
                    current=event.state;strong+=event.strong;weak+=event.weak
            except IllegalAction:
                continue
            number=len(helpers)
            sign=lambda s:(s.value>0)-(s.value<0)
            delta=number*sign(current)+centers*sign(guide.state)
            active=bool(number and (fan or hits))
            mask=(1<<(2 if fan else hits-1)) if active else 0
            selected=guides+tuple(p.player_key for p in helpers if active)
            cost=(number*strong+centers*guide.strong,int(active),
                  number*int(state.value==0 and current.value!=0)+centers*int(state.value==0 and guide.state.value!=0),
                  -number*int(state.value!=0 and current.value==0)-centers*int(state.value!=0 and guide.state.value==0),
                  number*(int(fan)+hits)+centers,number*weak+centers*guide.weak,
                  -sum(1<<(size-1-positions[key]) for key in selected))
            yield delta,centers,mask,cost,(guides,tuple(p.player_key for p in helpers),fan,hits)


def _plan(path, mode):
    guides=tuple(sorted((key for centers,*_ in path for key in centers),key=lambda k:tuple(map(int,k[1:].split('p')))))
    orders={key:i+1 for i,key in enumerate(guides)}
    actions=[]
    for centers,helpers,fan,hits in path:
        actions.extend(PlayerAction(key,False,(orders[key],)) for key in centers)
        circles=(1,3) if hits==2 else (1,) if hits else ()
        if fan or hits:actions.extend(PlayerAction(key,fan,circles) for key in helpers)
    actions.sort(key=lambda a:tuple(map(int,a.player_key[1:].split('p'))))
    return SolverPlan(mode,'after_tank',tuple(actions),guides,current_snapshot=True,command_style=True)


def solve_commands(players,mode='all'):
    """Search whole-group commands first, then minimally adjust a near solution.

    DP dominance is exact for the grouped search: equal headcounts, guide
    counts, creation flags and command masks have the same future possibilities. Whole-group
    rules do not compete with per-person action counts. Refinement is bounded
    to three promising templates and never invents success without a replay.
    """
    if mode not in ('all','fan','circle'):raise ValueError('Unknown analysis mode')
    ordered=sorted(players,key=lambda p:tuple(map(int,p.player_key[1:].split('p'))))
    groups=defaultdict(list)
    for p in ordered:
        if p.alive:
            value=(p.stacks or 0)*(-1 if p.color=='black' else 1) if p.color!='none' else 0
            if abs(value)>=5:return SearchResult(None,None,'已有五层，不采用自爆救场。')
            groups[value,p.isolated].append(p)
    positions={p.player_key:i for i,p in enumerate(ordered)}
    dp={(0,0,0,False):((0,)*7,())};explored=0
    for (value,isolated),members in groups.items():
        options=list(_group_options(members,BuffState(value,isolated),mode,positions,len(ordered)))
        updated={}
        for (difference,centers,mask,created),(cost,path) in dp.items():
            for delta,count,command,next_cost,option in options:
                if centers+count>3:continue
                explored+=1
                key=(difference+delta,centers+count,mask|command,created or bool(next_cost[2]))
                candidate=tuple(a+b for a,b in zip(cost,next_cost))
                if key not in updated or candidate<updated[key][0]:
                    updated[key]=(candidate,path+(option,))
        dp=updated
    candidates=[]
    for (delta,centers,mask,created),(cost,path) in dp.items():
        if centers!=3:continue
        score=(cost[0],mask.bit_count(),int(created),cost[1],int(bool(mask&2)),cost[2],abs(delta),*cost[3:])
        candidates.append((abs(delta),score,path))
    solutions=[]
    for delta,score,path in sorted((c for c in candidates if c[0]<=2),key=lambda c:c[1]):
        plan=_plan(path,mode);simulation=simulate_plan(ordered,plan)
        if simulation.valid:
            result=SearchResult(plan,simulation,explored=explored)
            operational=_operational_score(ordered,plan,simulation)
            solutions.append((operational,result))
            if operational[:3]==(0,0,0) or operational[:3]==(0,1,0):return result
            break
    # Start from a near-balanced, uniform group instruction. Find the fewest
    # exceptions to that instruction while keeping the same numbered guides.
    simple=[c for c in candidates if c[1][1]<=1]
    for delta,score,path in sorted(simple,key=lambda c:(c[0],c[1]))[:3]:
        baseline=_plan(path,mode)
        preferences={a.player_key:(a.fan,len(a.circles)) for a in baseline.actions}
        result=solve(ordered,mode,current_snapshot=True,preferred_actions=preferences,fixed_centers=baseline.centers)
        explored+=result.explored
        if result.plan is not None:
            plan=replace(result.plan,command_baseline=baseline.actions)
            solutions.append((_operational_score(ordered,plan,result.simulation),replace(result,plan=plan,explored=explored)))
    if solutions:return min(solutions,key=lambda item:item[0])[1]
    # Historical search can still diagnose a mathematical solution that needs
    # more complicated positioning; callers label it explicitly as such.
    result=solve(ordered,mode,current_snapshot=True)
    result.explored+=explored
    return result


def _operational_score(players,plan,simulation):
    guides=set(plan.centers);actions={a.player_key:a for a in plan.actions}
    categories=defaultdict(set);verbs=set()
    for p in players:
        if not p.alive or p.player_key in guides or p.isolated:continue
        action=actions.get(p.player_key,PlayerAction(p.player_key,False))
        behavior=(action.fan,action.circles)
        categories[p.color,p.stacks].add(behavior)
        if action.fan or action.circles:verbs.add(behavior)
    created=sum(p.alive and p.color=='none' and simulation.final_states[p.player_key].value!=0 for p in players)
    partial=sum(len(behaviors)>1 for behaviors in categories.values())
    selectors=sum(sum(fan or bool(circles) for fan,circles in behaviors) for behaviors in categories.values())
    return (simulation.strong_cancellations,len(verbs),int(created>0),partial,selectors,
            sum(max(0,len(circles)-1) for fan,circles in verbs),created,simulation.difference,
            sum(s.alive and s.value!=0 for s in simulation.final_states.values()),
            sum(a.fan+len(a.circles) for a in plan.actions))


def command_instruction(players,plan):
    """One clause per common action; numbered guides use the standing convention."""
    guides=set(plan.centers);actions={a.player_key:a for a in plan.actions}
    groups=defaultdict(list)
    for p in sorted(players,key=lambda p:tuple(map(int,p.player_key[1:].split('p')))):
        if p.alive and p.player_key not in guides and not p.isolated:
            groups[p.color,p.stacks].append(p)
    clauses=defaultdict(list)
    for (color,stacks),members in groups.items():
        label='无Buff' if color=='none' else ('红' if color=='red' else '黑')+'一二三四'[stacks-1]
        subsets=defaultdict(list)
        for p in members:
            a=actions.get(p.player_key,PlayerAction(p.player_key,False))
            subsets[a.fan,a.circles].append(p.player_key)
        for (fan,circles),keys in subsets.items():
            if not fan and not circles:continue
            selector=label if len(keys)==len(members) else f'标记{len(keys)}{label}'
            verb=('先吃扇，' if fan and circles else '吃扇' if fan else '')
            verb+=('跟'+'→'.join(map(str,circles))) if circles else ''
            clauses[verb].append(selector)
    def selector_order(label):
        layer=next((i for i,char in enumerate('一二三四') if char in label),0)
        return (label.startswith('标记'),label.startswith('无'),label.startswith('黑'),layer,label)
    text='；'.join('、'.join(sorted(labels,key=selector_order))+verb for verb,labels in clauses.items())
    if text:return text+'；其余避圈'
    guide_types=defaultdict(int)
    for p in players:
        if p.player_key in guides:
            label='隔绝' if p.isolated else '无Buff' if p.color=='none' else ('红' if p.color=='red' else '黑')+'一二三四'[p.stacks-1]
            guide_types[label]+=1
    return '、'.join(f'{n}{label}' for label,n in guide_types.items())+'排圈；其余避圈'

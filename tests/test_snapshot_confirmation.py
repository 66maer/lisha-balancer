from lisha_overlay.snapshot_confirmation import SnapshotConfirmation


def ready():
    c=SnapshotConfirmation()
    assert c.update(('A',),0.)==('confirming',None)
    assert c.update(('A',),.1)==('confirmed',('A',))
    return c


def test_holds_the_entire_old_snapshot_then_confirms_a_persistent_change():
    c=ready()
    assert c.update(('B',),.2)==('held',('A',))
    assert c.update(('B',),.3)==('confirmed',('B',))


def test_mixed_changes_never_create_an_unobserved_team():
    c=ready()
    assert c.update(('X','Y'),.2)[1]==('A',)
    assert c.update(('Z','W'),.3)[1]==('A',)
    assert c.update(('Z','W'),.4)==('confirmed',('Z','W'))


def test_an_obscured_frame_revokes_marks_and_requires_fresh_confirmation():
    c=ready()
    assert c.update(('A',),.2,trustworthy=False)==('unverified',None)
    assert c.update(('A',),.3)==('confirming',None)
    assert c.update(('A',),.4)==('confirmed',('A',))


def test_hold_expires_and_duplicate_capture_does_not_count_twice():
    c=SnapshotConfirmation()
    assert c.update(('A',),0.)==('confirming',None)
    assert c.update(('A',),0.)==('confirming',None)
    assert c.update(('A',),.1)[0]=='confirmed'
    for i,t in enumerate((.2,.3,.4,.5)):
        status,snapshot=c.update((i,),t)
    assert status=='confirming' and snapshot is None


def test_seek_gap_and_nonfinite_time_cannot_keep_old_results():
    c=ready()
    assert c.update(('A',),1.)==('confirming',None)
    assert c.update(('A',),.1)==('confirming',None)
    assert c.update(('A',),float('nan'))==('unverified',None)

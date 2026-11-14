"""Settlement headcount limits shared by planning and display."""

SAFE_TOLERANCE = {'normal': 2, 'heroic': 1}
FATAL_DIFFERENCE = {'normal': 5, 'heroic': 3}


def settlement_level(difference: int, difficulty: str) -> str:
    difference = abs(difference)
    if difference <= SAFE_TOLERANCE[difficulty]:
        return 'safe'
    if difference < FATAL_DIFFERENCE[difficulty]:
        return 'danger'
    return 'fatal'
